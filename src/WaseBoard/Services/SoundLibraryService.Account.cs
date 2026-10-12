using System;
using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;

namespace WaseBoard.Services
{
    // Compte Discord : fermeture de la session.
    public partial class SoundLibraryService
    {
        /// <summary>Le serveur a-t-il confirmé la fermeture de la session ?</summary>
        public enum LogoutOutcome
        {
            /// <summary>Session fermée côté serveur (ses jetons Discord y sont supprimés) ET sur cet ordinateur.</summary>
            Done,

            /// <summary>Déconnecté sur cet ordinateur, mais le serveur n'a pas répondu ou ne connaît pas la route (serveur
            /// ancien) : la session expirera d'elle-même côté serveur.</summary>
            ServerNotConfirmed,
        }

        /// <summary>
        /// Se déconnecte de Discord : demande au serveur d'oublier la session (POST /oauth/logout, 5 s au plus) puis efface
        /// la connexion locale — même si le serveur ne répond pas, l'utilisateur doit pouvoir se déconnecter. L'appel
        /// n'utilise pas SendAsync : un refus ne doit pas déclencher l'invite de reconnexion (SessionInvalidated).
        /// </summary>
        public async Task<LogoutOutcome> LogoutAsync()
        {
            var outcome = LogoutOutcome.ServerNotConfirmed;
            if (!string.IsNullOrEmpty(Settings.DiscordSessionToken))
            {
                try
                {
                    using var request = CreateRequest(HttpMethod.Post, "/oauth/logout");
                    using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
                    using var response = await _http.SendAsync(request, timeout.Token);
                    if (response.IsSuccessStatusCode) outcome = LogoutOutcome.Done;
                }
                catch
                {
                    // Serveur injoignable : on se déconnecte quand même localement.
                }
            }

            Settings.DiscordSessionToken = null;
            Settings.DiscordUserId = null;
            Settings.DiscordUsername = null;
            Settings.DiscordAvatarUrl = null;
            SaveSettings();
            return outcome;
        }
    }
}
