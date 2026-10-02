using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;
using WaseBoard.Models;

namespace WaseBoard.Services
{
    /// <summary>Client HTTP du serveur WaseBoard (catalogue, upload, lecture, activité partagée)
    /// et gestion des préférences locales (favoris, catégories, ordre, raccourcis, volumes).</summary>
    public partial class SoundLibraryService
    {
        private readonly string _appDataFolder;
        private readonly string _cacheFolder;
        private readonly string _settingsFilePath;
        private readonly HttpClient _http = new() { Timeout = TimeSpan.FromSeconds(15) };

        public AppSettings Settings { get; private set; } = new();

        /// <summary>Dossier de cache local (fichiers audio, et mini-waveforms), pour les services qui doivent y écrire en plus.</summary>
        public string CacheFolder => _cacheFolder;

        /// <summary>Détail de la dernière erreur réseau rencontrée, pour affichage/diagnostic.</summary>
        public string? LastErrorDetail { get; private set; }

        private class CatalogEntry
        {
            public string id { get; set; } = "";
            public string name { get; set; } = "";
            public string extension { get; set; } = ".wav";

            /// <summary>SHA-256 du contenu, calculé côté serveur à l'upload. Absent sur les sons
            /// uploadés avant l'ajout de la détection de doublons (null, ne matche jamais).</summary>
            public string? hash { get; set; }

            /// <summary>Emoji du son, partagé entre tous les utilisateurs (contrairement à
            /// favoris/catégories/volume/raccourcis, qui restent des préférences locales).</summary>
            public string? emoji { get; set; }

            /// <summary>Guilde Discord d'origine (celle où ce son a été uploadé).</summary>
            public string? guild_id { get; set; }

            /// <summary>Id Discord de l'auteur ; absent sur les sons uploadés avant les rôles.</summary>
            public string? uploaded_by { get; set; }

            /// <summary>Calculés par le serveur pour la session qui demande le catalogue.</summary>
            public bool is_mine { get; set; }
            public bool can_edit { get; set; }
        }

        private static SoundItem ToSoundItem(CatalogEntry entry) => new()
        {
            Id = entry.id,
            Name = entry.name,
            Extension = entry.extension,
            ContentHash = entry.hash,
            Emoji = string.IsNullOrEmpty(entry.emoji) ? null : entry.emoji,
            GuildId = entry.guild_id,
            UploadedBy = entry.uploaded_by,
            IsMine = entry.is_mine,
            CanEdit = entry.can_edit
        };

        /// <summary>Message d'erreur lisible d'une réponse serveur : le champ "error" du corps JSON s'il
        /// existe (les refus de droits y expliquent le motif), sinon le texte de repli.</summary>
        private static async Task<string> ReadServerErrorAsync(HttpResponseMessage response, string fallback)
        {
            try
            {
                var body = await response.Content.ReadAsStringAsync();
                using var doc = JsonDocument.Parse(body);
                if (doc.RootElement.ValueKind == JsonValueKind.Object
                    && doc.RootElement.TryGetProperty("error", out var error)
                    && error.ValueKind == JsonValueKind.String
                    && !string.IsNullOrWhiteSpace(error.GetString()))
                    return error.GetString()!;
            }
            catch
            {
                // Corps absent ou non JSON : on garde le texte de repli.
            }
            return fallback;
        }

        private class CatalogResponse
        {
            public List<CatalogEntry> sounds { get; set; } = new();
        }

        public SoundLibraryService()
        {
            _appDataFolder = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                AppIdentity.DataFolderName);
            _cacheFolder = Path.Combine(_appDataFolder, "Cache");
            _settingsFilePath = Path.Combine(_appDataFolder, "settings.json");

            Directory.CreateDirectory(_cacheFolder);
        }

        // ---------- Réglages locaux ----------

        public void Load()
        {
            LoadFromDisk();
            MigrateAppearance();
        }

        /// <summary>Première ouverture depuis l'arrivée des palettes (PaletteId absent) : adopte la palette
        /// par défaut et désactive « suivre l'accent Windows » — par défaut à vrai jusque-là, il aurait
        /// masqué l'accent de la nouvelle palette. Reste modifiable ensuite dans Paramètres > Apparence.</summary>
        private void MigrateAppearance()
        {
            if (Settings.PaletteId is not null) return;
            Settings.PaletteId = PalettePresets.DefaultId;
            Settings.FollowSystemAccent = false;
            // L'ancien écran d'Apparence enregistrait toujours le fond violet par défaut, même sans
            // personnalisation : ce n'est pas un vrai choix, et il masquerait la nouvelle palette.
            if (string.Equals(Settings.BackgroundColorHex, "#1E1E2E", StringComparison.OrdinalIgnoreCase))
                Settings.BackgroundColorHex = null;
            SaveSettings();
        }

        private void LoadFromDisk()
        {
            if (File.Exists(_settingsFilePath))
            {
                try
                {
                    var json = File.ReadAllText(_settingsFilePath);
                    Settings = JsonSerializer.Deserialize<AppSettings>(json) ?? new AppSettings();
                    ApplySecretsAfterLoad();
                    return;
                }
                catch
                {
                    // settings.json illisible/corrompu : on retombe sur la sauvegarde ci-dessous
                    // plutôt que de repartir avec des réglages vides.
                }
            }

            var backupPath = _settingsFilePath + ".bak";
            if (!File.Exists(backupPath)) return;
            try
            {
                var backupJson = File.ReadAllText(backupPath);
                Settings = JsonSerializer.Deserialize<AppSettings>(backupJson) ?? new AppSettings();
                ApplySecretsAfterLoad();
            }
            catch { /* sauvegarde également illisible : on repart de réglages par défaut */ }
        }

        /// <summary>Déchiffre les secrets persistés (DPAPI) dans les champs en mémoire, et migre
        /// une éventuelle installation existante dont ServerToken était encore en clair — lu une
        /// fois depuis l'ancien champ, puis immédiatement re-sauvegardé chiffré, sans quoi le
        /// renommage du champ JSON aurait silencieusement perdu ce jeton pour tout le monde.</summary>
        private void ApplySecretsAfterLoad()
        {
            var migrated = false;
            if (string.IsNullOrEmpty(Settings.EncryptedServerToken) && !string.IsNullOrEmpty(Settings.LegacyServerTokenPlaintext))
            {
                Settings.ServerToken = Settings.LegacyServerTokenPlaintext;
                migrated = true;
            }
            else
            {
                Settings.ServerToken = SecretProtector.Unprotect(Settings.EncryptedServerToken);
            }
            Settings.LegacyServerTokenPlaintext = null;

            Settings.DiscordSessionToken = SecretProtector.Unprotect(Settings.EncryptedDiscordSessionToken);

            if (migrated) SaveSettings();
        }

        /// <summary>Vide le cache local de fichiers audio téléchargés — à appeler quand on change
        /// de serveur (ex: lien waseboard://connect vers une autre instance) : les fichiers mis en
        /// cache appartiennent à l'ancien serveur et n'ont plus lieu d'être conservés (par ailleurs,
        /// deux instances différentes pourraient en théorie réutiliser le même ID de son).</summary>
        public void ClearLocalCache()
        {
            try
            {
                foreach (var file in Directory.GetFiles(_cacheFolder))
                {
                    try { File.Delete(file); } catch { /* best-effort, fichier verrouillé ou déjà supprimé */ }
                }
            }
            catch { /* dossier inaccessible : rien de grave, juste de l'espace disque non libéré */ }
        }

        public void SaveSettings()
        {
            // Sauvegarde de secours (un seul niveau) avant d'écraser : en cas de perte de données
            // (ex: deux instances de l'app ouvertes en même temps, la dernière à sauvegarder
            // écrasant l'autre), settings.json.bak garde toujours l'état juste avant la dernière
            // écriture — récupérable manuellement en le renommant en settings.json.
            if (File.Exists(_settingsFilePath))
            {
                try { File.Copy(_settingsFilePath, _settingsFilePath + ".bak", overwrite: true); }
                catch { /* best-effort, ne doit jamais empêcher l'enregistrement normal */ }
            }

            Settings.EncryptedServerToken = SecretProtector.Protect(Settings.ServerToken);
            Settings.EncryptedDiscordSessionToken = SecretProtector.Protect(Settings.DiscordSessionToken);

            var json = JsonSerializer.Serialize(Settings, new JsonSerializerOptions { WriteIndented = true });
            File.WriteAllText(_settingsFilePath, json);
        }

        public void ToggleFavorite(SoundItem item)
        {
            item.IsFavorite = !item.IsFavorite;

            if (item.IsFavorite && !Settings.FavoriteSoundIds.Contains(item.Id))
                Settings.FavoriteSoundIds.Add(item.Id);
            else if (!item.IsFavorite)
                Settings.FavoriteSoundIds.Remove(item.Id);

            SaveSettings();
        }

        public void SetHotkey(SoundItem item, string? hotkey)
        {
            item.Hotkey = hotkey;
            if (string.IsNullOrEmpty(hotkey))
                Settings.SoundHotkeys.Remove(item.Id);
            else
                Settings.SoundHotkeys[item.Id] = hotkey;
            SaveSettings();
        }

        public void SetVolume(SoundItem item, float volume)
        {
            item.Volume = volume;
            Settings.SoundVolumes[item.Id] = volume;
            SaveSettings();
        }

        // ---------- Ordre personnalisé des sons ----------

        public List<SoundItem> ApplyCustomOrder(List<SoundItem> items)
        {
            if (Settings.SoundOrder.Count == 0) return items;

            var byId = items.ToDictionary(i => i.Id);
            var ordered = new List<SoundItem>();

            foreach (var id in Settings.SoundOrder)
                if (byId.TryGetValue(id, out var item)) { ordered.Add(item); byId.Remove(id); }

            ordered.AddRange(byId.Values);
            return ordered;
        }

        public void SaveSoundOrder(IEnumerable<SoundItem> orderedItems)
        {
            Settings.SoundOrder = orderedItems.Select(i => i.Id).ToList();
            SaveSettings();
        }

        // ---------- Catégories personnalisées ----------

        public void CreateCategory(string name)
        {
            if (Settings.Categories.ContainsKey(name)) return;
            Settings.Categories[name] = new List<string>();
            Settings.CategoryOrder.Add(name);
            SaveSettings();
        }

        public void DeleteCategory(string name)
        {
            Settings.Categories.Remove(name);
            Settings.CategoryOrder.Remove(name);
            SaveSettings();
        }

        public void RenameCategory(string oldName, string newName)
        {
            if (!Settings.Categories.TryGetValue(oldName, out var soundIds) || Settings.Categories.ContainsKey(newName)) return;
            Settings.Categories.Remove(oldName);
            Settings.Categories[newName] = soundIds;

            var idx = Settings.CategoryOrder.IndexOf(oldName);
            if (idx >= 0) Settings.CategoryOrder[idx] = newName;

            SaveSettings();
        }

        public void SaveCategoryOrder(IEnumerable<string> orderedNames)
        {
            Settings.CategoryOrder = orderedNames.ToList();
            SaveSettings();
        }

        public void AddSoundToCategory(string category, SoundItem item)
        {
            if (!Settings.Categories.TryGetValue(category, out var list))
            {
                list = new List<string>();
                Settings.Categories[category] = list;
                if (!Settings.CategoryOrder.Contains(category)) Settings.CategoryOrder.Add(category);
            }
            if (!list.Contains(item.Id)) list.Add(item.Id);
            SaveSettings();
        }

        public void RemoveSoundFromCategory(string category, SoundItem item)
        {
            if (Settings.Categories.TryGetValue(category, out var list))
                list.Remove(item.Id);
            SaveSettings();
        }

        /// <summary>Ordre des catégories personnelles (+ partagées si des GuildId sont fournis).
        /// Auto-corrige CategoryOrder : ajoute les clés inconnues, retire les obsolètes.</summary>
        public List<string> GetCategoriesInOrder(IEnumerable<string>? sharedGuildIds = null)
        {
            var validKeys = new HashSet<string>(Settings.Categories.Keys);
            if (sharedGuildIds is not null)
                foreach (var id in sharedGuildIds) validKeys.Add(id);

            var order = Settings.CategoryOrder.Where(validKeys.Contains).ToList();
            var known = new HashSet<string>(order);
            var changed = order.Count != Settings.CategoryOrder.Count;

            foreach (var key in validKeys)
                if (known.Add(key)) { order.Add(key); changed = true; }

            if (changed)
            {
                Settings.CategoryOrder = order;
                SaveSettings();
            }
            return order;
        }

        public List<SoundItem> GetSoundsInCategory(string category, IEnumerable<SoundItem> allSounds)
        {
            if (!Settings.Categories.TryGetValue(category, out var ids)) return new List<SoundItem>();
            var set = new HashSet<string>(ids);
            return allSounds.Where(s => set.Contains(s.Id)).ToList();
        }

        public async Task<bool> RenameSoundAsync(SoundItem item, string newName)
        {
            LastErrorDetail = null;
            try
            {
                using var request = CreateRequest(HttpMethod.Patch, $"/sounds/{item.Id}");
                var payload = JsonSerializer.Serialize(new { name = newName });
                request.Content = new StringContent(payload, Encoding.UTF8, "application/json");

                using var response = await SendAsync(request);
                if (!response.IsSuccessStatusCode)
                {
                    LastErrorDetail = await ReadServerErrorAsync(response, $"Renommage échoué ({(int)response.StatusCode}).");
                    return false;
                }

                item.Name = newName;
                return true;
            }
            catch (Exception ex)
            {
                LastErrorDetail = ex.Message;
                return false;
            }
        }

        /// <summary>Emoji partagé entre tous les utilisateurs (contrairement à favoris/catégories/
        /// volume/raccourcis, préférences locales) : assigné par quelqu'un, visible et modifiable
        /// par tous, donc stocké côté serveur plutôt que dans Settings.</summary>
        public async Task<bool> SetEmojiAsync(SoundItem item, string? emoji)
        {
            LastErrorDetail = null;
            var trimmed = string.IsNullOrWhiteSpace(emoji) ? null : emoji.Trim();
            try
            {
                using var request = CreateRequest(HttpMethod.Patch, $"/sounds/{item.Id}");
                var payload = JsonSerializer.Serialize(new { emoji = trimmed ?? "" });
                request.Content = new StringContent(payload, Encoding.UTF8, "application/json");

                using var response = await SendAsync(request);
                if (!response.IsSuccessStatusCode)
                {
                    LastErrorDetail = await ReadServerErrorAsync(response, $"Changement d'emoji échoué ({(int)response.StatusCode}).");
                    return false;
                }

                item.Emoji = trimmed;
                return true;
            }
            catch (Exception ex)
            {
                LastErrorDetail = ex.Message;
                return false;
            }
        }

        // ---------- Communication serveur ----------

        private string BaseUrl => Settings.ServerUrl.TrimEnd('/');

        private HttpRequestMessage CreateRequest(HttpMethod method, string path)
        {
            var request = new HttpRequestMessage(method, BaseUrl + path);
            if (!string.IsNullOrEmpty(Settings.ServerToken))
                request.Headers.Add("X-WaseBoard-Token", Settings.ServerToken);
            if (!string.IsNullOrEmpty(Settings.DiscordSessionToken))
                request.Headers.Authorization = new System.Net.Http.Headers.AuthenticationHeaderValue("Bearer", Settings.DiscordSessionToken);
            return request;
        }

        /// <summary>Levé une seule fois quand une session Discord est détectée invalide/révoquée
        /// (401 "invalid_session"/"revoked") — jamais pour "missing_session", qui signifie
        /// simplement "pas encore connecté" et n'a rien d'anormal.</summary>
        public event Action? SessionInvalidated;

        /// <summary>Point de passage unique pour tous les appels serveur : détecte une session
        /// Discord devenue invalide et l'efface IMMÉDIATEMENT en mémoire, pour que les appels
        /// suivants (y compris ceux déjà programmés par les sondages 300ms/3s) cessent d'envoyer
        /// un Bearer connu-invalide au lieu de re-déclencher l'évènement en boucle.</summary>
        private readonly object _sessionInvalidationLock = new();

        private async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request)
        {
            var response = await _http.SendAsync(request);
            if (response.StatusCode == HttpStatusCode.Unauthorized && Settings.DiscordSessionToken is not null)
            {
                var body = await response.Content.ReadAsStringAsync();
                if (body.Contains("invalid_session") || body.Contains("revoked"))
                {
                    // Plusieurs requêtes quasi simultanées (sondages 300ms/3s) peuvent toutes
                    // recevoir ce 401 avant que l'une d'elles ait fini d'effacer la session —
                    // le verrou garantit qu'une seule déclenche réellement l'évènement/la sauvegarde.
                    var shouldNotify = false;
                    lock (_sessionInvalidationLock)
                    {
                        if (Settings.DiscordSessionToken is not null)
                        {
                            Settings.DiscordSessionToken = null;
                            shouldNotify = true;
                        }
                    }
                    if (shouldNotify)
                    {
                        SaveSettings();
                        SessionInvalidated?.Invoke();
                    }
                }
            }
            return response;
        }

        public async Task<List<SoundItem>> FetchCatalogAsync()
        {
            LastErrorDetail = null;
            if (string.IsNullOrEmpty(Settings.DiscordSessionToken))
            {
                LastErrorDetail = "Non connecté à Discord.";
                return new List<SoundItem>();
            }
            try
            {
                using var request = CreateRequest(HttpMethod.Get, "/sounds");
                using var response = await SendAsync(request);

                if (!response.IsSuccessStatusCode)
                {
                    LastErrorDetail = $"Le serveur a répondu {(int)response.StatusCode}.";
                    return new List<SoundItem>();
                }

                var json = await response.Content.ReadAsStringAsync();
                var catalog = JsonSerializer.Deserialize<CatalogResponse>(json) ?? new CatalogResponse();

                var items = catalog.sounds.Select(entry =>
                {
                    var item = ToSoundItem(entry);
                    item.IsFavorite = Settings.FavoriteSoundIds.Contains(entry.id);
                    item.Hotkey = Settings.SoundHotkeys.TryGetValue(entry.id, out var hk) ? hk : null;
                    item.Volume = Settings.SoundVolumes.TryGetValue(entry.id, out var vol) ? vol : 1.0f;
                    return item;
                }).ToList();

                return ApplyCustomOrder(items);
            }
            catch (Exception ex)
            {
                LastErrorDetail = ex.Message;
                return new List<SoundItem>();
            }
        }

        public async Task<string?> GetOrDownloadCachedFileAsync(SoundItem item)
        {
            var cachePath = Path.Combine(_cacheFolder, item.Id + item.Extension);
            if (File.Exists(cachePath)) return cachePath;

            try
            {
                using var request = CreateRequest(HttpMethod.Get, $"/sounds/{item.Id}/file");
                using var response = await SendAsync(request);

                if (!response.IsSuccessStatusCode)
                {
                    LastErrorDetail = $"Téléchargement échoué ({(int)response.StatusCode}).";
                    return null;
                }

                await using var fileStream = File.Create(cachePath);
                await response.Content.CopyToAsync(fileStream);
                return cachePath;
            }
            catch (Exception ex)
            {
                LastErrorDetail = ex.Message;
                return null;
            }
        }

        public async Task PrefetchAllAsync(IEnumerable<SoundItem> items, int maxConcurrent = 4)
        {
            using var semaphore = new System.Threading.SemaphoreSlim(maxConcurrent);
            var tasks = items.Select(async item =>
            {
                await semaphore.WaitAsync();
                try { await GetOrDownloadCachedFileAsync(item); }
                finally { semaphore.Release(); }
            });
            await Task.WhenAll(tasks);
        }

        /// <summary>SHA-256 d'un fichier local, en minuscules hexadécimal — même format que le hash calculé
        /// côté serveur à l'upload, pour permettre au client de détecter un doublon AVANT d'envoyer le fichier.</summary>
        public static string ComputeFileHash(string filePath)
        {
            using var sha256 = SHA256.Create();
            using var stream = File.OpenRead(filePath);
            return Convert.ToHexString(sha256.ComputeHash(stream)).ToLowerInvariant();
        }

        public async Task<SoundItem?> UploadSoundAsync(string localFilePath, string name, string guildId)
        {
            LastErrorDetail = null;
            try
            {
                using var request = CreateRequest(HttpMethod.Post, "/sounds");
                using var form = new MultipartFormDataContent();
                using var fileStream = File.OpenRead(localFilePath);
                using var fileContent = new StreamContent(fileStream);

                form.Add(new StringContent(name, Encoding.UTF8), "name");
                form.Add(fileContent, "file", Path.GetFileName(localFilePath));
                form.Add(new StringContent(guildId, Encoding.UTF8), "guild_id");
                request.Content = form;

                using var response = await SendAsync(request);
                if (!response.IsSuccessStatusCode)
                {
                    LastErrorDetail = await ReadServerErrorAsync(response, $"Envoi échoué ({(int)response.StatusCode}).");
                    return null;
                }

                var json = await response.Content.ReadAsStringAsync();
                var entry = JsonSerializer.Deserialize<CatalogEntry>(json);
                return entry is null ? null : ToSoundItem(entry);
            }
            catch (Exception ex)
            {
                LastErrorDetail = ex.Message;
                return null;
            }
        }

        public async Task<bool> DeleteSoundAsync(SoundItem item)
        {
            LastErrorDetail = null;
            try
            {
                using var request = CreateRequest(HttpMethod.Delete, $"/sounds/{item.Id}");
                using var response = await SendAsync(request);

                if (!response.IsSuccessStatusCode)
                {
                    LastErrorDetail = await ReadServerErrorAsync(response, $"Suppression échouée ({(int)response.StatusCode}).");
                    return false;
                }

                var cachePath = Path.Combine(_cacheFolder, item.Id + item.Extension);
                if (File.Exists(cachePath)) File.Delete(cachePath);

                Settings.FavoriteSoundIds.Remove(item.Id);
                Settings.SoundHotkeys.Remove(item.Id);
                Settings.SoundVolumes.Remove(item.Id);
                Settings.SoundOrder.Remove(item.Id);
                foreach (var list in Settings.Categories.Values) list.Remove(item.Id);
                SaveSettings();

                return true;
            }
            catch (Exception ex)
            {
                LastErrorDetail = ex.Message;
                return false;
            }
        }

        /// <summary>Déclenche la lecture du son côté serveur. Le salon Discord est déduit automatiquement de votre présence vocale — rien à choisir.</summary>
        public async Task<bool> PlayOnServerAsync(string soundId, float volume)
        {
            LastErrorDetail = null;
            try
            {
                using var request = CreateRequest(HttpMethod.Post, "/play");
                var payload = JsonSerializer.Serialize(new { id = soundId, volume });
                request.Content = new StringContent(payload, Encoding.UTF8, "application/json");

                using var response = await SendAsync(request);
                if (!response.IsSuccessStatusCode)
                {
                    LastErrorDetail = await ReadServerErrorAsync(response, $"Le serveur a répondu {(int)response.StatusCode}.");
                    return false;
                }
                return true;
            }
            catch (Exception ex)
            {
                LastErrorDetail = ex.Message;
                return false;
            }
        }

        /// <summary>Coupe tous les sons en cours sur le salon Discord ciblé (déduit de votre présence vocale).</summary>
        public async Task<bool> StopAllOnServerAsync()
        {
            LastErrorDetail = null;
            try
            {
                using var request = CreateRequest(HttpMethod.Post, "/stop");

                using var response = await SendAsync(request);
                if (!response.IsSuccessStatusCode)
                {
                    LastErrorDetail = await ReadServerErrorAsync(response, $"Le serveur a répondu {(int)response.StatusCode}.");
                    return false;
                }
                return true;
            }
            catch (Exception ex)
            {
                LastErrorDetail = ex.Message;
                return false;
            }
        }

        /// <summary>Résultat détaillé de GetServerStatusAsync : le serveur répond "200 + connected:
        /// false" aussi bien pour "jeton correct mais bot pas en vocal" que pour d'autres cas — sans
        /// cette distinction, l'UI ne pouvait afficher qu'un message générique regroupant à tort
        /// une vraie panne réseau, un jeton faux, et le cas bénin "personne n'a encore fait /join".</summary>
        public enum ServerStatusResult
        {
            /// <summary>Serveur joignable, jeton correct, bot connecté à un salon vocal.</summary>
            Connected,
            /// <summary>Serveur joignable, jeton correct, mais le bot n'est dans aucun salon vocal pour l'instant.</summary>
            BotNotInVoice,
            /// <summary>Le jeton (shared_secret) est incorrect.</summary>
            Unauthorized,
            /// <summary>Serveur injoignable (réseau, DNS, TLS...) ou erreur inattendue — voir LastErrorDetail.</summary>
            Unreachable
        }

        public async Task<(ServerStatusResult Result, string? Channel)> GetServerStatusAsync()
        {
            LastErrorDetail = null;
            try
            {
                using var request = CreateRequest(HttpMethod.Get, "/status");
                using var response = await SendAsync(request);

                if (response.StatusCode == HttpStatusCode.Unauthorized)
                    return (ServerStatusResult.Unauthorized, null);

                if (!response.IsSuccessStatusCode)
                {
                    LastErrorDetail = $"Le serveur a répondu {(int)response.StatusCode}.";
                    return (ServerStatusResult.Unreachable, null);
                }

                var json = await response.Content.ReadAsStringAsync();
                using var doc = JsonDocument.Parse(json);
                var connected = doc.RootElement.TryGetProperty("connected", out var c) && c.GetBoolean();
                string? channel = doc.RootElement.TryGetProperty("channel", out var ch) && ch.ValueKind == JsonValueKind.String
                    ? ch.GetString() : null;
                return (connected ? ServerStatusResult.Connected : ServerStatusResult.BotNotInVoice, channel);
            }
            catch (Exception ex)
            {
                LastErrorDetail = ex.Message;
                return (ServerStatusResult.Unreachable, null);
            }
        }

        /// <summary>
        /// Statut vocal en direct, strict : contrairement à GetServerStatusAsync (qui peut supposer
        /// "vous êtes là" par défaut s'il n'y a qu'un seul salon connecté), celui-ci ne renvoie
        /// "connecté" que si l'utilisateur est RÉELLEMENT présent dans ce salon vocal en ce moment —
        /// utilisé pour l'indicateur de la barre latérale, qui ne doit jamais mentir.
        /// </summary>
        public async Task<(bool Connected, string? Channel, string? GuildId, string? GuildName, List<UserActivity> ChannelMembers, string? Error)> GetLiveVoiceStatusAsync()
        {
            if (string.IsNullOrEmpty(Settings.DiscordSessionToken)) return (false, null, null, null, new(), null);

            try
            {
                using var request = CreateRequest(HttpMethod.Get, "/status?strict=1");
                using var response = await SendAsync(request);
                var raw = await response.Content.ReadAsStringAsync();

                if (!response.IsSuccessStatusCode)
                    return (false, null, null, null, new(), $"Le serveur a répondu {(int)response.StatusCode}.");

                JsonDocument doc;
                try { doc = JsonDocument.Parse(raw); }
                catch
                {
                    // Réponse qui n'est pas du JSON (ex: page d'erreur 404 générique d'aiohttp) :
                    // presque toujours le signe que le serveur tourne encore avec une ancienne
                    // version de server.py, sans cette route/ce champ.
                    return (false, null, null, null, new(),
                        "Réponse inattendue du serveur — server.py a-t-il bien été redéployé avec la dernière version ?");
                }
                using (doc)
                {
                    var connected = doc.RootElement.TryGetProperty("connected", out var c) && c.GetBoolean();
                    string? channel = doc.RootElement.TryGetProperty("channel", out var ch) && ch.ValueKind == JsonValueKind.String ? ch.GetString() : null;
                    string? guildId = doc.RootElement.TryGetProperty("guild_id", out var gi) && gi.ValueKind == JsonValueKind.String ? gi.GetString() : null;
                    string? guildName = doc.RootElement.TryGetProperty("guild_name", out var g) && g.ValueKind == JsonValueKind.String ? g.GetString() : null;

                    var members = new List<UserActivity>();
                    if (doc.RootElement.TryGetProperty("channel_members", out var membersArray))
                        foreach (var m in membersArray.EnumerateArray())
                            members.Add(new UserActivity
                            {
                                UserId = m.GetProperty("user_id").GetString() ?? "",
                                Username = m.GetProperty("username").GetString() ?? "",
                                AvatarUrl = m.GetProperty("avatar_url").GetString() ?? ""
                            });

                    return (connected, channel, guildId, guildName, members, null);
                }
            }
            catch (Exception ex)
            {
                return (false, null, null, null, new(), ex.Message);
            }
        }

        /// <summary>Demande au bot de rejoindre le salon vocal où vous vous trouvez actuellement, sans passer par /join dans Discord.</summary>
        public async Task<(bool Success, string? GuildName, string? ChannelName, string? Error)> JoinMyChannelAsync()
        {
            if (string.IsNullOrEmpty(Settings.DiscordSessionToken))
                return (false, null, null, "Non connecté à Discord.");

            try
            {
                using var request = CreateRequest(HttpMethod.Post, "/join-my-channel");

                using var response = await SendAsync(request);
                var raw = await response.Content.ReadAsStringAsync();

                JsonDocument doc;
                try { doc = JsonDocument.Parse(raw); }
                catch
                {
                    return (false, null, null,
                        "Réponse inattendue du serveur (pas du JSON) — server.py a-t-il bien été " +
                        "redéployé avec la dernière version (route /join-my-channel) ? Extrait reçu : " +
                        raw.Substring(0, Math.Min(120, raw.Length)));
                }

                using (doc)
                {
                    if (!response.IsSuccessStatusCode)
                    {
                        var error = doc.RootElement.TryGetProperty("error", out var e) ? e.GetString() : $"Le serveur a répondu {(int)response.StatusCode}.";
                        return (false, null, null, error);
                    }

                    var guildName = doc.RootElement.TryGetProperty("guild_name", out var g) ? g.GetString() : null;
                    var channelName = doc.RootElement.TryGetProperty("channel_name", out var c) ? c.GetString() : null;
                    return (true, guildName, channelName, null);
                }
            }
            catch (Exception ex)
            {
                return (false, null, null, ex.Message);
            }
        }

        /// <summary>Récupère l'identifiant client OAuth2 Discord de cette instance serveur —
        /// propre à chaque déploiement auto-hébergé, nécessaire pour construire l'URL d'autorisation.</summary>
        public async Task<string?> FetchOAuthClientIdAsync()
        {
            try
            {
                using var request = CreateRequest(HttpMethod.Get, "/oauth/client-id");
                using var response = await SendAsync(request);
                if (!response.IsSuccessStatusCode) return null;

                var json = await response.Content.ReadAsStringAsync();
                using var doc = JsonDocument.Parse(json);
                return doc.RootElement.TryGetProperty("client_id", out var c) ? c.GetString() : null;
            }
            catch (Exception ex)
            {
                LastErrorDetail = ex.Message;
                return null;
            }
        }

        /// <summary>Échange un code d'autorisation Discord (obtenu via le navigateur système) contre
        /// une session WaseBoard — voir DiscordOAuthService pour le flux complet.</summary>
        public async Task<(bool Success, string? SessionToken, string? UserId, string? Username, string? AvatarUrl, string? Error)> ExchangeOAuthCodeAsync(string code, string redirectUri)
        {
            try
            {
                using var request = CreateRequest(HttpMethod.Post, "/oauth/exchange");
                var payload = JsonSerializer.Serialize(new { code, redirect_uri = redirectUri });
                request.Content = new StringContent(payload, Encoding.UTF8, "application/json");

                using var response = await SendAsync(request);
                var raw = await response.Content.ReadAsStringAsync();
                using var doc = JsonDocument.Parse(raw);

                if (!response.IsSuccessStatusCode)
                {
                    var error = doc.RootElement.TryGetProperty("error", out var e) ? e.GetString() : $"Le serveur a répondu {(int)response.StatusCode}.";
                    return (false, null, null, null, null, error);
                }

                return (
                    true,
                    doc.RootElement.GetProperty("session_token").GetString(),
                    doc.RootElement.GetProperty("user_id").GetString(),
                    doc.RootElement.GetProperty("username").GetString(),
                    doc.RootElement.TryGetProperty("avatar_url", out var a) && a.ValueKind == JsonValueKind.String ? a.GetString() : null,
                    null
                );
            }
            catch (Exception ex)
            {
                return (false, null, null, null, null, ex.Message);
            }
        }

        /// <summary>
        /// Récupère l'activité en cours (qui joue quoi) et la liste des utilisateurs actuellement
        /// en ligne (application ouverte), pour le highlight/avatars et la barre "en ligne" des
        /// catégories partagées. Signale au passage notre propre présence au serveur.
        /// </summary>
        public async Task<(Dictionary<string, List<UserActivity>> Activity, List<UserActivity> Online)> GetActivityAsync()
        {
            try
            {
                using var request = CreateRequest(HttpMethod.Get, "/activity");
                using var response = await SendAsync(request);
                if (!response.IsSuccessStatusCode) return (new(), new());

                var json = await response.Content.ReadAsStringAsync();
                using var doc = JsonDocument.Parse(json);
                var activity = new Dictionary<string, List<UserActivity>>();

                foreach (var prop in doc.RootElement.GetProperty("activity").EnumerateObject())
                {
                    var users = new List<UserActivity>();
                    foreach (var u in prop.Value.EnumerateArray())
                        users.Add(ParseUserActivity(u));
                    activity[prop.Name] = users;
                }

                var online = new List<UserActivity>();
                if (doc.RootElement.TryGetProperty("online", out var onlineArray))
                    foreach (var u in onlineArray.EnumerateArray())
                        online.Add(ParseUserActivity(u));

                return (activity, online);
            }
            catch
            {
                return (new(), new()); // le sondage échoue silencieusement : effet visuel, pas une fonction critique
            }
        }

        private static UserActivity ParseUserActivity(JsonElement u) => new()
        {
            UserId = u.GetProperty("user_id").GetString() ?? "",
            Username = u.GetProperty("username").GetString() ?? "",
            AvatarUrl = u.GetProperty("avatar_url").GetString() ?? ""
        };

        // ---------- Catégorie partagée automatique par serveur Discord ----------
        // Une catégorie par Discord dont l'utilisateur est membre, sans nom à choisir ni bouton
        // de création : son existence découle directement de la présence du serveur Discord.

        /// <summary>Une catégorie partagée automatique, une par serveur Discord dont l'utilisateur est membre.</summary>
        public class SharedCategoryInfo
        {
            public string GuildId { get; set; } = "";
            public string GuildName { get; set; } = "";
            public string? IconUrl { get; set; }
            public List<string> SoundIds { get; set; } = new();

            /// <summary>Vous administrez ce serveur (propriétaire, permission Administrateur ou rôle désigné) —
            /// calculé par le serveur ; sert à afficher les actions d'admin, le serveur re-vérifie chaque requête.</summary>
            public bool IsAdmin { get; set; }

            /// <summary>Vous pouvez y uploader des sons (pas restreint aux admins, pas bloqué).</summary>
            public bool CanUpload { get; set; } = true;
        }

        /// <summary>
        /// Liste, pour chaque serveur Discord dont l'utilisateur est membre, sa catégorie
        /// partagée automatique (créée à la volée si elle est vide). Retourne une liste vide si
        /// l'identité Discord n'est pas configurée ou si l'utilisateur n'est membre d'aucun
        /// serveur connu du bot.
        /// </summary>
        public async Task<List<SharedCategoryInfo>> FetchSharedCategoriesAsync()
        {
            if (string.IsNullOrEmpty(Settings.DiscordSessionToken)) return new();

            try
            {
                using var request = CreateRequest(HttpMethod.Get, "/my-guilds");
                using var response = await SendAsync(request);
                if (!response.IsSuccessStatusCode) return new();

                var json = await response.Content.ReadAsStringAsync();
                using var doc = JsonDocument.Parse(json);
                var guilds = doc.RootElement.GetProperty("guilds").EnumerateArray()
                    .Select(g => (
                        Id: g.GetProperty("id").GetString() ?? "",
                        Name: g.GetProperty("name").GetString() ?? "",
                        IconUrl: g.TryGetProperty("icon_url", out var icon) && icon.ValueKind == JsonValueKind.String ? icon.GetString() : null,
                        IsAdmin: g.TryGetProperty("is_admin", out var admin) && admin.ValueKind == JsonValueKind.True,
                        // Un serveur plus ancien (sans les rôles) n'envoie pas "can_upload" : permissif par défaut.
                        CanUpload: !g.TryGetProperty("can_upload", out var canUpload) || canUpload.ValueKind != JsonValueKind.False
                    ))
                    .ToList();

                var result = new List<SharedCategoryInfo>();
                foreach (var (guildId, guildName, iconUrl, isAdmin, canUploadHere) in guilds)
                {
                    var soundIds = await FetchSharedCategorySoundIdsAsync(guildId);
                    result.Add(new SharedCategoryInfo
                    {
                        GuildId = guildId, GuildName = guildName, IconUrl = iconUrl, SoundIds = soundIds,
                        IsAdmin = isAdmin, CanUpload = canUploadHere
                    });
                }
                return result;
            }
            catch (Exception ex) { LastErrorDetail = ex.Message; return new(); }
        }

        private async Task<List<string>> FetchSharedCategorySoundIdsAsync(string guildId)
        {
            try
            {
                using var request = CreateRequest(HttpMethod.Get, $"/shared-categories?guild_id={Uri.EscapeDataString(guildId)}");
                using var response = await SendAsync(request);
                if (!response.IsSuccessStatusCode) return new();

                var json = await response.Content.ReadAsStringAsync();
                using var doc = JsonDocument.Parse(json);
                return doc.RootElement.GetProperty("sound_ids").EnumerateArray().Select(x => x.GetString() ?? "").ToList();
            }
            catch (Exception ex) { LastErrorDetail = ex.Message; return new(); }
        }

        /// <summary>Ajoute ou retire un son de la catégorie partagée automatique d'un serveur Discord.</summary>
        public async Task<bool> SetSharedCategorySoundAsync(string guildId, SoundItem item, bool add)
        {
            LastErrorDetail = null;
            try
            {
                using var request = CreateRequest(HttpMethod.Post, "/shared-categories/sounds");
                var payload = JsonSerializer.Serialize(new { guild_id = guildId, sound_id = item.Id, action = add ? "add" : "remove" });
                request.Content = new StringContent(payload, Encoding.UTF8, "application/json");
                using var response = await SendAsync(request);
                if (!response.IsSuccessStatusCode)
                    LastErrorDetail = await ReadServerErrorAsync(response, $"Le serveur a répondu {(int)response.StatusCode}.");
                return response.IsSuccessStatusCode;
            }
            catch (Exception ex) { LastErrorDetail = ex.Message; return false; }
        }
    }
}
