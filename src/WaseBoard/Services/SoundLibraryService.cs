using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;
using WaseBoard.Models;

namespace WaseBoard.Services
{
    /// <summary>
    /// Gère le catalogue de sons partagé, hébergé sur le serveur WaseBoard : récupération
    /// de la liste, upload, suppression, déclenchement/arrêt de la lecture côté bot Discord,
    /// activité partagée (highlight + avatars), et mise en cache locale des fichiers pour
    /// l'aperçu. Gère aussi les préférences locales propres à chaque utilisateur : identité
    /// Discord, favoris, catégories, ordre d'affichage, raccourcis, volumes et emojis par son.
    /// </summary>
    public class SoundLibraryService
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
        }

        private class CatalogResponse
        {
            public List<CatalogEntry> sounds { get; set; } = new();
        }

        public SoundLibraryService()
        {
            _appDataFolder = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                "WaseBoard");
            _cacheFolder = Path.Combine(_appDataFolder, "Cache");
            _settingsFilePath = Path.Combine(_appDataFolder, "settings.json");

            Directory.CreateDirectory(_cacheFolder);
        }

        // ---------- Réglages locaux ----------

        public void Load()
        {
            if (File.Exists(_settingsFilePath))
            {
                try
                {
                    var json = File.ReadAllText(_settingsFilePath);
                    Settings = JsonSerializer.Deserialize<AppSettings>(json) ?? new AppSettings();
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
            }
            catch { /* sauvegarde également illisible : on repart de réglages par défaut */ }
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

        public void SetEmoji(SoundItem item, string? emoji)
        {
            item.Emoji = string.IsNullOrWhiteSpace(emoji) ? null : emoji.Trim();
            if (string.IsNullOrEmpty(item.Emoji))
                Settings.SoundEmojis.Remove(item.Id);
            else
                Settings.SoundEmojis[item.Id] = item.Emoji;
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

        /// <summary>
        /// Ordre d'affichage des catégories personnelles, et des catégories partagées si leurs
        /// GuildId sont passés en paramètre (sinon personnelles uniquement, ex: menu "Ajouter à une
        /// catégorie" qui les liste dans deux groupes séparés). Auto-corrige et persiste
        /// CategoryOrder : ajoute les clés inconnues (nouvelle catégorie, nouveau serveur Discord
        /// rejoint) en fin de liste, retire les clés obsolètes (catégorie supprimée, serveur quitté).
        /// </summary>
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

                using var response = await _http.SendAsync(request);
                if (!response.IsSuccessStatusCode)
                {
                    LastErrorDetail = $"Renommage échoué ({(int)response.StatusCode}).";
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

        // ---------- Communication serveur ----------

        private string BaseUrl => Settings.ServerUrl.TrimEnd('/');

        private HttpRequestMessage CreateRequest(HttpMethod method, string path)
        {
            var request = new HttpRequestMessage(method, BaseUrl + path);
            if (!string.IsNullOrEmpty(Settings.ServerToken))
                request.Headers.Add("X-WaseBoard-Token", Settings.ServerToken);
            return request;
        }

        public async Task<List<SoundItem>> FetchCatalogAsync()
        {
            LastErrorDetail = null;
            try
            {
                using var request = CreateRequest(HttpMethod.Get, "/sounds");
                using var response = await _http.SendAsync(request);

                if (!response.IsSuccessStatusCode)
                {
                    LastErrorDetail = $"Le serveur a répondu {(int)response.StatusCode}.";
                    return new List<SoundItem>();
                }

                var json = await response.Content.ReadAsStringAsync();
                var catalog = JsonSerializer.Deserialize<CatalogResponse>(json) ?? new CatalogResponse();

                var items = catalog.sounds.Select(entry => new SoundItem
                {
                    Id = entry.id,
                    Name = entry.name,
                    Extension = entry.extension,
                    IsFavorite = Settings.FavoriteSoundIds.Contains(entry.id),
                    Hotkey = Settings.SoundHotkeys.TryGetValue(entry.id, out var hk) ? hk : null,
                    Volume = Settings.SoundVolumes.TryGetValue(entry.id, out var vol) ? vol : 1.0f,
                    Emoji = Settings.SoundEmojis.TryGetValue(entry.id, out var emoji) ? emoji : null
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
                using var response = await _http.SendAsync(request);

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

        public async Task<SoundItem?> UploadSoundAsync(string localFilePath, string name)
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
                request.Content = form;

                using var response = await _http.SendAsync(request);
                if (!response.IsSuccessStatusCode)
                {
                    LastErrorDetail = $"Envoi échoué ({(int)response.StatusCode}) : {await response.Content.ReadAsStringAsync()}";
                    return null;
                }

                var json = await response.Content.ReadAsStringAsync();
                var entry = JsonSerializer.Deserialize<CatalogEntry>(json);
                if (entry is null) return null;

                return new SoundItem { Id = entry.id, Name = entry.name, Extension = entry.extension };
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
                using var response = await _http.SendAsync(request);

                var cachePath = Path.Combine(_cacheFolder, item.Id + item.Extension);
                if (File.Exists(cachePath)) File.Delete(cachePath);

                Settings.FavoriteSoundIds.Remove(item.Id);
                Settings.SoundHotkeys.Remove(item.Id);
                Settings.SoundVolumes.Remove(item.Id);
                Settings.SoundEmojis.Remove(item.Id);
                Settings.SoundOrder.Remove(item.Id);
                foreach (var list in Settings.Categories.Values) list.Remove(item.Id);
                SaveSettings();

                if (!response.IsSuccessStatusCode)
                {
                    LastErrorDetail = $"Suppression échouée ({(int)response.StatusCode}).";
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

        /// <summary>Déclenche la lecture du son côté serveur. Le salon Discord est déduit automatiquement de votre présence vocale — rien à choisir.</summary>
        public async Task<bool> PlayOnServerAsync(string soundId, float volume)
        {
            LastErrorDetail = null;
            try
            {
                using var request = CreateRequest(HttpMethod.Post, "/play");
                var payload = JsonSerializer.Serialize(new { id = soundId, volume, user_id = Settings.DiscordUserId });
                request.Content = new StringContent(payload, Encoding.UTF8, "application/json");

                using var response = await _http.SendAsync(request);
                if (!response.IsSuccessStatusCode)
                {
                    LastErrorDetail = $"Le serveur a répondu {(int)response.StatusCode} : {await response.Content.ReadAsStringAsync()}";
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
                var payload = JsonSerializer.Serialize(new { user_id = Settings.DiscordUserId });
                request.Content = new StringContent(payload, Encoding.UTF8, "application/json");

                using var response = await _http.SendAsync(request);
                if (!response.IsSuccessStatusCode)
                {
                    LastErrorDetail = $"Le serveur a répondu {(int)response.StatusCode} : {await response.Content.ReadAsStringAsync()}";
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

        public async Task<(bool Connected, string? Channel)> GetServerStatusAsync()
        {
            LastErrorDetail = null;
            try
            {
                var query = string.IsNullOrEmpty(Settings.DiscordUserId) ? "" : $"?user_id={Uri.EscapeDataString(Settings.DiscordUserId)}";
                using var request = CreateRequest(HttpMethod.Get, "/status" + query);
                using var response = await _http.SendAsync(request);
                if (!response.IsSuccessStatusCode)
                {
                    LastErrorDetail = $"Le serveur a répondu {(int)response.StatusCode}.";
                    return (false, null);
                }

                var json = await response.Content.ReadAsStringAsync();
                using var doc = JsonDocument.Parse(json);
                var connected = doc.RootElement.TryGetProperty("connected", out var c) && c.GetBoolean();
                string? channel = doc.RootElement.TryGetProperty("channel", out var ch) && ch.ValueKind == JsonValueKind.String
                    ? ch.GetString() : null;
                return (connected, channel);
            }
            catch (Exception ex)
            {
                LastErrorDetail = ex.Message;
                return (false, null);
            }
        }

        /// <summary>
        /// Statut vocal en direct, strict : contrairement à GetServerStatusAsync (qui peut supposer
        /// "vous êtes là" par défaut s'il n'y a qu'un seul salon connecté), celui-ci ne renvoie
        /// "connecté" que si l'utilisateur est RÉELLEMENT présent dans ce salon vocal en ce moment —
        /// utilisé pour l'indicateur de la barre latérale, qui ne doit jamais mentir.
        /// </summary>
        public async Task<(bool Connected, string? Channel, string? GuildName, List<UserActivity> ChannelMembers, string? Error)> GetLiveVoiceStatusAsync()
        {
            if (string.IsNullOrEmpty(Settings.DiscordUserId)) return (false, null, null, new(), null);

            try
            {
                using var request = CreateRequest(HttpMethod.Get, $"/status?strict=1&user_id={Uri.EscapeDataString(Settings.DiscordUserId)}");
                using var response = await _http.SendAsync(request);
                var raw = await response.Content.ReadAsStringAsync();

                if (!response.IsSuccessStatusCode)
                    return (false, null, null, new(), $"Le serveur a répondu {(int)response.StatusCode}.");

                JsonDocument doc;
                try { doc = JsonDocument.Parse(raw); }
                catch
                {
                    // Réponse qui n'est pas du JSON (ex: page d'erreur 404 générique d'aiohttp) :
                    // presque toujours le signe que le serveur tourne encore avec une ancienne
                    // version de server.py, sans cette route/ce champ.
                    return (false, null, null, new(),
                        "Réponse inattendue du serveur — server.py a-t-il bien été redéployé avec la dernière version ?");
                }
                using (doc)
                {
                    var connected = doc.RootElement.TryGetProperty("connected", out var c) && c.GetBoolean();
                    string? channel = doc.RootElement.TryGetProperty("channel", out var ch) && ch.ValueKind == JsonValueKind.String ? ch.GetString() : null;
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

                    return (connected, channel, guildName, members, null);
                }
            }
            catch (Exception ex)
            {
                return (false, null, null, new(), ex.Message);
            }
        }

        /// <summary>Demande au bot de rejoindre le salon vocal où vous vous trouvez actuellement, sans passer par /join dans Discord.</summary>
        public async Task<(bool Success, string? GuildName, string? ChannelName, string? Error)> JoinMyChannelAsync()
        {
            if (string.IsNullOrEmpty(Settings.DiscordUserId))
                return (false, null, null, "Identité Discord non configurée dans les Paramètres.");

            try
            {
                using var request = CreateRequest(HttpMethod.Post, "/join-my-channel");
                var payload = JsonSerializer.Serialize(new { user_id = Settings.DiscordUserId });
                request.Content = new StringContent(payload, Encoding.UTF8, "application/json");

                using var response = await _http.SendAsync(request);
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

        /// <summary>Vérifie qu'un ID Discord correspond bien à un membre connu du bot, et retourne son nom/avatar si oui.</summary>
        public async Task<(bool Found, string? Username, string? AvatarUrl, string? GuildName, string? Error)> VerifyUserIdAsync(string userId)
        {
            try
            {
                using var request = CreateRequest(HttpMethod.Get, $"/verify-user?user_id={Uri.EscapeDataString(userId)}");
                using var response = await _http.SendAsync(request);
                if (!response.IsSuccessStatusCode)
                    return (false, null, null, null, $"Le serveur a répondu {(int)response.StatusCode}.");

                var json = await response.Content.ReadAsStringAsync();
                using var doc = JsonDocument.Parse(json);
                var found = doc.RootElement.TryGetProperty("found", out var f) && f.GetBoolean();

                if (!found)
                {
                    var error = doc.RootElement.TryGetProperty("error", out var e) ? e.GetString() : null;
                    return (false, null, null, null, error);
                }

                return (
                    true,
                    doc.RootElement.GetProperty("username").GetString(),
                    doc.RootElement.GetProperty("avatar_url").GetString(),
                    doc.RootElement.GetProperty("guild_name").GetString(),
                    null
                );
            }
            catch (Exception ex)
            {
                return (false, null, null, null, ex.Message);
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
                var query = string.IsNullOrEmpty(Settings.DiscordUserId) ? "" : $"?user_id={Uri.EscapeDataString(Settings.DiscordUserId)}";
                using var request = CreateRequest(HttpMethod.Get, "/activity" + query);
                using var response = await _http.SendAsync(request);
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
        }

        /// <summary>
        /// Liste, pour chaque serveur Discord dont l'utilisateur est membre, sa catégorie
        /// partagée automatique (créée à la volée si elle est vide). Retourne une liste vide si
        /// l'identité Discord n'est pas configurée ou si l'utilisateur n'est membre d'aucun
        /// serveur connu du bot.
        /// </summary>
        public async Task<List<SharedCategoryInfo>> FetchSharedCategoriesAsync()
        {
            if (string.IsNullOrEmpty(Settings.DiscordUserId)) return new();

            try
            {
                using var request = CreateRequest(HttpMethod.Get, $"/my-guilds?user_id={Uri.EscapeDataString(Settings.DiscordUserId)}");
                using var response = await _http.SendAsync(request);
                if (!response.IsSuccessStatusCode) return new();

                var json = await response.Content.ReadAsStringAsync();
                using var doc = JsonDocument.Parse(json);
                var guilds = doc.RootElement.GetProperty("guilds").EnumerateArray()
                    .Select(g => (
                        Id: g.GetProperty("id").GetString() ?? "",
                        Name: g.GetProperty("name").GetString() ?? "",
                        IconUrl: g.TryGetProperty("icon_url", out var icon) && icon.ValueKind == JsonValueKind.String ? icon.GetString() : null
                    ))
                    .ToList();

                var result = new List<SharedCategoryInfo>();
                foreach (var (guildId, guildName, iconUrl) in guilds)
                {
                    var soundIds = await FetchSharedCategorySoundIdsAsync(guildId);
                    result.Add(new SharedCategoryInfo { GuildId = guildId, GuildName = guildName, IconUrl = iconUrl, SoundIds = soundIds });
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
                using var response = await _http.SendAsync(request);
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
            try
            {
                using var request = CreateRequest(HttpMethod.Post, "/shared-categories/sounds");
                var payload = JsonSerializer.Serialize(new { guild_id = guildId, sound_id = item.Id, action = add ? "add" : "remove" });
                request.Content = new StringContent(payload, Encoding.UTF8, "application/json");
                using var response = await _http.SendAsync(request);
                if (!response.IsSuccessStatusCode) LastErrorDetail = $"Le serveur a répondu {(int)response.StatusCode}.";
                return response.IsSuccessStatusCode;
            }
            catch (Exception ex) { LastErrorDetail = ex.Message; return false; }
        }
    }
}
