using System;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text.Json;
using System.Threading.Tasks;

namespace WaseBoard.Services
{
    public sealed record UpdateCheckResult(bool Available, string? LatestVersion, string? ReleaseUrl, string? Error);

    /// <summary>
    /// Vérifie la dernière release GitHub publique du dépôt et la compare à la version de
    /// l'assembly courante. Ne lève jamais : un échec réseau se traduit par un résultat avec
    /// Error rempli (même esprit que SoundLibraryService.GetServerStatusAsync/VerifyUserIdAsync).
    /// </summary>
    public static class UpdateCheckService
    {
        private const string ReleasesApiUrl = "https://api.github.com/repos/salsi64/WaseBoard/releases/latest";

        private static readonly HttpClient Http = CreateClient();

        private static HttpClient CreateClient()
        {
            var client = new HttpClient { Timeout = TimeSpan.FromSeconds(10) };
            // L'API GitHub refuse les requêtes sans User-Agent.
            client.DefaultRequestHeaders.UserAgent.Add(new ProductInfoHeaderValue("WaseBoard", "1"));
            client.DefaultRequestHeaders.Accept.Add(new MediaTypeWithQualityHeaderValue("application/vnd.github+json"));
            return client;
        }

        public static async Task<UpdateCheckResult> CheckForUpdateAsync(Version currentVersion)
        {
            try
            {
                using var response = await Http.GetAsync(ReleasesApiUrl);
                if (!response.IsSuccessStatusCode)
                    return new UpdateCheckResult(false, null, null, $"Le serveur a répondu {(int)response.StatusCode}.");

                var json = await response.Content.ReadAsStringAsync();
                using var doc = JsonDocument.Parse(json);

                var tagName = doc.RootElement.TryGetProperty("tag_name", out var tag) ? tag.GetString() : null;
                var releaseUrl = doc.RootElement.TryGetProperty("html_url", out var url) ? url.GetString() : null;
                if (string.IsNullOrWhiteSpace(tagName))
                    return new UpdateCheckResult(false, null, null, "Réponse inattendue de GitHub.");

                var versionText = tagName.StartsWith('v') ? tagName[1..] : tagName;
                if (!Version.TryParse(versionText, out var latestVersion))
                    return new UpdateCheckResult(false, null, null, $"Numéro de version illisible : {tagName}.");

                var available = latestVersion > currentVersion;
                return new UpdateCheckResult(available, versionText, releaseUrl, null);
            }
            catch (Exception ex)
            {
                return new UpdateCheckResult(false, null, null, ex.Message);
            }
        }
    }
}
