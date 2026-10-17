using System;
using System.Net.Http;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace WaseBoard.Services
{
    // Copie des catégories personnelles sur le serveur : la version web et l'Activité Discord n'ont pas accès aux réglages de ce PC.
    public partial class SoundLibraryService
    {
        /// <summary>Attente avant l'envoi : une série de modifications (glisser des sons, en ajouter plusieurs) ne fait qu'un envoi.</summary>
        private static readonly TimeSpan CategoryMirrorDelay = TimeSpan.FromSeconds(2);

        private readonly CategoryMirror _categoryMirror = new();
        private readonly SemaphoreSlim _categoryMirrorSending = new(1, 1);   // un seul envoi à la fois : jamais une copie ancienne après une récente
        private CancellationTokenSource? _categoryMirrorPending;

        /// <summary>
        /// À appeler quand les réglages ou le catalogue viennent d'être lus ou écrits : si les catégories ont changé depuis le dernier envoi,
        /// programme un envoi (PUT /me/categories) dans quelques secondes. Silencieux en cas d'échec : le prochain enregistrement ou le
        /// prochain lancement réessaie, et rien ne change pour l'application elle-même.
        /// </summary>
        private void QueueCategoryMirror()
        {
            if (string.IsNullOrEmpty(Settings.DiscordSessionToken) || !ServerSupports("personal_categories")) return;

            var target = $"{BaseUrl}|{Settings.DiscordUserId}";
            var payload = _categoryMirror.PayloadToSend(target, Settings.Categories, Settings.CategoryOrder);
            if (payload is null) return;

            var pending = new CancellationTokenSource();
            Interlocked.Exchange(ref _categoryMirrorPending, pending)?.Cancel();
            _ = Task.Run(() => SendCategoryMirrorAsync(target, payload, pending.Token));
        }

        private async Task SendCategoryMirrorAsync(string target, string payload, CancellationToken cancel)
        {
            try
            {
                await Task.Delay(CategoryMirrorDelay, cancel);
                await _categoryMirrorSending.WaitAsync(cancel);
                try
                {
                    using var request = CreateRequest(HttpMethod.Put, "/me/categories");
                    request.Content = new StringContent(payload, Encoding.UTF8, "application/json");
                    using var response = await SendAsync(request);
                    if (response.IsSuccessStatusCode) _categoryMirror.MarkSent(target, payload);
                }
                finally
                {
                    _categoryMirrorSending.Release();
                }
            }
            catch
            {
                // Annulé (une modification plus récente a pris le relais) ou hors connexion : sans conséquence.
            }
        }
    }
}
