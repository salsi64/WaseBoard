using System;
using System.Collections.Generic;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Threading.Tasks;

namespace WaseBoard.Services
{
    // Appels du panel d'administration (routes /admin/guilds/{id}/..., réservées aux admins de
    // la guilde — le serveur refuse tout autre appelant en 403). Réutilise CreateRequest /
    // SendAsync / ReadServerErrorAsync de la partie principale de la classe.
    public partial class SoundLibraryService
    {
        /// <summary>Réglages d'une guilde, tels que stockés par le serveur (0 = désactivé / illimité).</summary>
        public class AdminSettings
        {
            [JsonPropertyName("admin_role_id")] public string? AdminRoleId { get; set; }
            [JsonPropertyName("upload_admins_only")] public bool UploadAdminsOnly { get; set; }
            [JsonPropertyName("max_sounds")] public int MaxSounds { get; set; }
            [JsonPropertyName("max_file_mb")] public int MaxFileMb { get; set; }
            [JsonPropertyName("max_duration_s")] public int MaxDurationSeconds { get; set; }
            [JsonPropertyName("max_total_mb")] public int MaxTotalMb { get; set; }
            [JsonPropertyName("play_rate_per_min")] public int PlayRatePerMinute { get; set; }
        }

        /// <summary>Consommation actuelle d'une guilde (renvoyée par les serveurs récents, absente sinon).</summary>
        public class AdminUsage
        {
            [JsonPropertyName("sounds")] public int Sounds { get; set; }
            [JsonPropertyName("total_mb")] public double TotalMb { get; set; }
        }

        public class AdminRole
        {
            [JsonPropertyName("id")] public string Id { get; set; } = "";
            [JsonPropertyName("name")] public string Name { get; set; } = "";
        }

        public class AdminBlockedUser
        {
            [JsonPropertyName("user_id")] public string UserId { get; set; } = "";
            [JsonPropertyName("username")] public string? Username { get; set; }

            /// <summary>Nom à afficher : le pseudo si le membre est connu du serveur, sinon son identifiant.</summary>
            [JsonIgnore] public string DisplayName => string.IsNullOrEmpty(Username) ? $"Membre {UserId}" : Username;
        }

        public class AdminSettingsInfo
        {
            [JsonPropertyName("settings")] public AdminSettings Settings { get; set; } = new();
            [JsonPropertyName("roles")] public List<AdminRole> Roles { get; set; } = new();
            [JsonPropertyName("blocked")] public List<AdminBlockedUser> Blocked { get; set; } = new();

            /// <summary>Plafonds imposés par l'hébergeur du serveur (nom du réglage → valeur maximale). Vide = aucun.</summary>
            [JsonPropertyName("ceilings")] public Dictionary<string, int>? Ceilings { get; set; }

            /// <summary>Null pour un serveur antérieur aux quotas d'espace : le champ correspondant est alors masqué.</summary>
            [JsonPropertyName("usage")] public AdminUsage? Usage { get; set; }
        }

        public class AdminSound
        {
            [JsonPropertyName("id")] public string Id { get; set; } = "";
            [JsonPropertyName("name")] public string Name { get; set; } = "";
            [JsonPropertyName("emoji")] public string? Emoji { get; set; }
            [JsonPropertyName("uploaded_by")] public string? UploadedBy { get; set; }
            [JsonPropertyName("uploaded_by_name")] public string? UploadedByName { get; set; }
            [JsonPropertyName("uploaded_at")] public long? UploadedAt { get; set; }
            [JsonPropertyName("size")] public long? Size { get; set; }
            [JsonPropertyName("plays")] public int Plays { get; set; }
        }

        public class AdminTrashItem
        {
            [JsonPropertyName("id")] public string Id { get; set; } = "";
            [JsonPropertyName("name")] public string Name { get; set; } = "";
            [JsonPropertyName("emoji")] public string? Emoji { get; set; }
            [JsonPropertyName("uploaded_by_name")] public string? UploadedByName { get; set; }
            [JsonPropertyName("deleted_by")] public string? DeletedBy { get; set; }
            [JsonPropertyName("deleted_by_name")] public string? DeletedByName { get; set; }
            [JsonPropertyName("deleted_at")] public double DeletedAt { get; set; }

            /// <summary>Jours restants avant la purge définitive ; null si la corbeille n'est jamais purgée.</summary>
            [JsonPropertyName("days_left")] public int? DaysLeft { get; set; }
        }

        public class AdminTrashInfo
        {
            [JsonPropertyName("trash")] public List<AdminTrashItem> Items { get; set; } = new();
            [JsonPropertyName("retention_days")] public int RetentionDays { get; set; }
        }

        public class AdminStatSound
        {
            [JsonPropertyName("name")] public string Name { get; set; } = "";
            [JsonPropertyName("emoji")] public string? Emoji { get; set; }
            [JsonPropertyName("count")] public int Count { get; set; }
        }

        public class AdminStatUser
        {
            [JsonPropertyName("user_id")] public string UserId { get; set; } = "";
            [JsonPropertyName("username")] public string? Username { get; set; }
            [JsonPropertyName("avatar_url")] public string? AvatarUrl { get; set; }
            [JsonPropertyName("count")] public int Count { get; set; }
        }

        public class AdminStatDay
        {
            [JsonPropertyName("date")] public string Date { get; set; } = "";
            [JsonPropertyName("count")] public int Count { get; set; }
        }

        public class AdminStats
        {
            [JsonPropertyName("days")] public int Days { get; set; }
            [JsonPropertyName("total_plays")] public int TotalPlays { get; set; }
            [JsonPropertyName("top_sounds")] public List<AdminStatSound> TopSounds { get; set; } = new();
            [JsonPropertyName("top_users")] public List<AdminStatUser> TopUsers { get; set; } = new();
            [JsonPropertyName("per_day")] public List<AdminStatDay> PerDay { get; set; } = new();
        }

        public class AdminAuditEntry
        {
            [JsonPropertyName("ts")] public double Timestamp { get; set; }
            [JsonPropertyName("actor_name")] public string? ActorName { get; set; }
            [JsonPropertyName("action")] public string Action { get; set; } = "";
            [JsonPropertyName("sound_name")] public string? SoundName { get; set; }
            [JsonPropertyName("details")] public Dictionary<string, JsonElement>? Details { get; set; }
        }

        private class AdminSoundsResponse { [JsonPropertyName("sounds")] public List<AdminSound> Sounds { get; set; } = new(); }
        private class AdminAuditResponse { [JsonPropertyName("entries")] public List<AdminAuditEntry> Entries { get; set; } = new(); }
        private class AdminOk { [JsonPropertyName("status")] public string? Status { get; set; } }

        /// <summary>Envoie une requête admin et désérialise la réponse. Renvoie null (et renseigne
        /// LastErrorDetail avec le motif donné par le serveur) en cas d'échec.</summary>
        private async Task<T?> SendAdminAsync<T>(HttpMethod method, string path, object? body = null) where T : class
        {
            LastErrorDetail = null;
            try
            {
                using var request = CreateRequest(method, path);
                if (body is not null)
                    request.Content = new StringContent(JsonSerializer.Serialize(body), Encoding.UTF8, "application/json");

                using var response = await SendAsync(request);
                if (!response.IsSuccessStatusCode)
                {
                    LastErrorDetail = await ReadServerErrorAsync(response, $"Le serveur a répondu {(int)response.StatusCode}.");
                    return null;
                }
                return JsonSerializer.Deserialize<T>(await response.Content.ReadAsStringAsync());
            }
            catch (Exception ex)
            {
                LastErrorDetail = ex.Message;
                return null;
            }
        }

        private static string GuildPath(string guildId) => $"/admin/guilds/{Uri.EscapeDataString(guildId)}";

        public Task<AdminSettingsInfo?> GetAdminSettingsAsync(string guildId) =>
            SendAdminAsync<AdminSettingsInfo>(HttpMethod.Get, GuildPath(guildId) + "/settings");

        /// <summary>Enregistre les réglages de la guilde (la liste des membres bloqués a ses propres appels).</summary>
        public Task<AdminSettingsInfo?> UpdateAdminSettingsAsync(string guildId, AdminSettings settings) =>
            SendAdminAsync<AdminSettingsInfo>(HttpMethod.Put, GuildPath(guildId) + "/settings", new
            {
                admin_role_id = settings.AdminRoleId ?? "",
                upload_admins_only = settings.UploadAdminsOnly,
                max_sounds = settings.MaxSounds,
                max_file_mb = settings.MaxFileMb,
                max_duration_s = settings.MaxDurationSeconds,
                max_total_mb = settings.MaxTotalMb,
                play_rate_per_min = settings.PlayRatePerMinute
            });

        public Task<AdminSettingsInfo?> SetUploaderBlockedAsync(string guildId, string userId, bool blocked) =>
            SendAdminAsync<AdminSettingsInfo>(HttpMethod.Post, GuildPath(guildId) + "/blocked", new { user_id = userId, blocked });

        public async Task<List<AdminSound>?> GetAdminSoundsAsync(string guildId) =>
            (await SendAdminAsync<AdminSoundsResponse>(HttpMethod.Get, GuildPath(guildId) + "/sounds"))?.Sounds;

        public Task<AdminTrashInfo?> GetAdminTrashAsync(string guildId) =>
            SendAdminAsync<AdminTrashInfo>(HttpMethod.Get, GuildPath(guildId) + "/trash");

        public async Task<bool> RestoreFromTrashAsync(string guildId, string soundId) =>
            await SendAdminAsync<AdminOk>(HttpMethod.Post, GuildPath(guildId) + $"/trash/{Uri.EscapeDataString(soundId)}/restore") is not null;

        public Task<AdminStats?> GetAdminStatsAsync(string guildId, int days) =>
            SendAdminAsync<AdminStats>(HttpMethod.Get, GuildPath(guildId) + $"/stats?days={days}");

        public async Task<List<AdminAuditEntry>?> GetAdminAuditAsync(string guildId, int limit = 200) =>
            (await SendAdminAsync<AdminAuditResponse>(HttpMethod.Get, GuildPath(guildId) + $"/audit?limit={limit}"))?.Entries;
    }
}
