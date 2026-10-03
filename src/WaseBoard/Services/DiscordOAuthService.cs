using System;
using System.Diagnostics;
using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace WaseBoard.Services
{
    /// <summary>Flux de connexion Discord OAuth2 pour l'app desktop : ouvre le navigateur système
    /// sur la page d'autorisation Discord, attrape la redirection via un petit serveur HTTP local
    /// (Discord exige http(s) — http://127.0.0.1 est la seule exception à HTTPS), puis transmet le
    /// code obtenu au serveur WaseBoard (seul détenteur du client_secret Discord) pour l'échange
    /// réel. Voir server/README.md pour l'enregistrement de l'URL de redirection.</summary>
    public class DiscordOAuthService
    {
        // Doit être enregistré tel quel dans Discord Developer Portal > OAuth2 > Redirects.
        // Hors de la plage éphémère Windows (49152-65535) pour éviter une collision avec une
        // connexion sortante transitoire ; hors des ports courants d'outils de dev.
        public const int LoopbackPort = 48899;
        private static readonly string RedirectUri = $"http://127.0.0.1:{LoopbackPort}/callback/";

        private static readonly TimeSpan LoginTimeout = TimeSpan.FromMinutes(3);

        private readonly SoundLibraryService _library;

        public DiscordOAuthService(SoundLibraryService library) => _library = library;

        public record LoginResult(bool Success, string? SessionToken, string? UserId,
                                   string? Username, string? AvatarUrl, string? Error);

        public async Task<LoginResult> LoginAsync()
        {
            var clientId = await _library.FetchOAuthClientIdAsync();
            if (clientId is null)
                return new(false, null, null, null, null, "Client OAuth2 introuvable (serveur non configuré, ou injoignable).");

            var state = Convert.ToHexString(RandomNumberGenerator.GetBytes(16));

            using var listener = new HttpListener();
            listener.Prefixes.Add(RedirectUri);
            try
            {
                listener.Start();
            }
            catch (HttpListenerException ex)
            {
                return new(false, null, null, null, null,
                    $"Port {LoopbackPort} déjà utilisé (une connexion est-elle déjà en cours ?) : {ex.Message}");
            }

            var authorizeUrl = "https://discord.com/oauth2/authorize" +
                $"?client_id={Uri.EscapeDataString(clientId)}" +
                $"&redirect_uri={Uri.EscapeDataString(RedirectUri)}" +
                "&response_type=code&scope=identify" +
                $"&state={state}";

            try
            {
                Process.Start(new ProcessStartInfo(authorizeUrl) { UseShellExecute = true });
            }
            catch (Exception ex)
            {
                listener.Stop();
                return new(false, null, null, null, null, $"Impossible d'ouvrir le navigateur : {ex.Message}");
            }

            HttpListenerContext ctx;
            try
            {
                using var cts = new CancellationTokenSource(LoginTimeout);
                var getContextTask = listener.GetContextAsync();
                var timeoutTask = Task.Delay(Timeout.Infinite, cts.Token);
                var winner = await Task.WhenAny(getContextTask, timeoutTask);
                if (winner != getContextTask)
                    return new(false, null, null, null, null, "Délai dépassé — connexion non terminée dans le navigateur.");
                ctx = await getContextTask;
            }
            catch (Exception ex)
            {
                return new(false, null, null, null, null, ex.Message);
            }
            // Pas de listener.Stop() ici : la réponse HTTP au navigateur (ci-dessous) doit
            // s'écrire AVANT tout arrêt/disposition de l'écouteur, sinon le flux de réponse est
            // coupé en plein envoi (vu par le navigateur comme une connexion refusée/réinitialisée,
            // et peut faire planter l'appli avec un ObjectDisposedException sur ThreadPoolBoundHandle).
            // Le "using var listener" plus haut s'en charge proprement à la toute fin de la méthode.

            var code = ctx.Request.QueryString["code"];
            var receivedState = ctx.Request.QueryString["state"];
            var discordError = ctx.Request.QueryString["error"];
            try
            {
                await RespondToBrowserAsync(ctx, ok: code is not null && receivedState == state);
            }
            catch { /* best-effort : la réponse au navigateur n'est qu'un confort visuel, le code est déjà extrait */ }

            if (discordError is not null)
            {
                return new(false, null, null, null, null,
                    discordError == "access_denied" ? "Connexion annulée : autorisation refusée." : $"Discord : {discordError}");
            }
            if (receivedState != state)
                return new(false, null, null, null, null, "Échec de vérification de sécurité — réessayez.");
            if (code is null)
                return new(false, null, null, null, null, "Aucun code reçu de Discord.");

            var result = await _library.ExchangeOAuthCodeAsync(code, RedirectUri);
            return new(result.Success, result.SessionToken, result.UserId, result.Username, result.AvatarUrl, result.Error);
        }

        // Mêmes couleurs que la palette par défaut de l'appli (PalettePresets.NightBlue) et que la page
        // /connect du serveur (server.py, _CONNECT_PAGE_STYLE) : un fond blanc en pleine nuit serait
        // franchement désagréable pour qui vient de passer par le thème sombre de Discord et de l'appli.
        private const string PageStyle =
            "body{font-family:'Segoe UI',system-ui,sans-serif;background:#0f1624;color:#eaf0fa;margin:0;" +
            "display:flex;min-height:100vh;align-items:center;justify-content:center}" +
            "main{max-width:380px;padding:32px;text-align:center}" +
            "h1{font-size:20px;margin:0 0 10px}p{line-height:1.5;color:#b8c4d9;margin:0 0 24px}" +
            ".links{display:flex;flex-direction:column;gap:10px}" +
            "a.link{display:block;background:#182236;color:#eaf0fa;text-decoration:none;padding:10px 16px;" +
            "border-radius:8px;font-size:13px;border:1px solid #26324a}" +
            "a.link:hover{border-color:#38bdf8}";

        // Site et communauté du projet, pas spécifiques à ce serveur WaseBoard — utile même si la
        // personne qui vous a invité n'a pas (encore) tout configuré.
        private const string FooterLinks =
            """
            <div class="links">
                <a class="link" href="https://discord.gg/HAGTNGFyQd">💬 Rejoindre le Discord WaseBoard</a>
                <a class="link" href="https://waseboard.salsi.bid/">🌐 Le site</a>
                <a class="link" href="https://github.com/salsi64/WaseBoard">🐙 Code source (GitHub)</a>
            </div>
            """;

        private static async Task RespondToBrowserAsync(HttpListenerContext ctx, bool ok)
        {
            var message = ok
                ? "Connexion réussie — vous pouvez fermer cet onglet et revenir dans WaseBoard."
                : "Échec de la connexion — vous pouvez fermer cet onglet et réessayer dans WaseBoard.";
            var html =
                $"""
                <!doctype html><html lang="fr"><head><meta charset="utf-8"><title>WaseBoard</title>
                <meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
                <style>{PageStyle}</style></head><body><main>
                <div style="font-size:36px;margin-bottom:8px">🎛️</div>
                <h1>WaseBoard</h1>
                <p>{message}</p>
                {FooterLinks}
                </main></body></html>
                """;
            var bytes = Encoding.UTF8.GetBytes(html);
            ctx.Response.ContentType = "text/html; charset=utf-8";
            ctx.Response.ContentLength64 = bytes.Length;
            await ctx.Response.OutputStream.WriteAsync(bytes);
            ctx.Response.OutputStream.Close();
        }
    }
}
