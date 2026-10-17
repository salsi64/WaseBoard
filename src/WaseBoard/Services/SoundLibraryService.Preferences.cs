using System;
using System.Collections.Generic;
using System.Linq;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace WaseBoard.Services
{
    // Synchronisation des préférences (favoris, couleurs, catégories personnelles et leur ordre) avec le serveur : la page web et l'Activité
    // Discord les modifient aussi. À chaque échange, l'application envoie son état ET l'état du serveur qu'elle avait reçu la fois d'avant ;
    // le serveur fusionne (server/preferences.py) et renvoie le résultat, qu'elle adopte. Rien ne se perd, même si on modifie des deux côtés.
    public partial class SoundLibraryService
    {
        private static readonly TimeSpan PreferencesDebounce = TimeSpan.FromSeconds(2);   // une série de modifications ne fait qu'un envoi
        private static readonly TimeSpan PreferencesPollEvery = TimeSpan.FromSeconds(60); // pour recevoir ce qui a été changé sur le web

        private readonly SemaphoreSlim _preferencesSending = new(1, 1);   // un seul échange à la fois
        private CancellationTokenSource? _preferencesPending;
        private Timer? _preferencesTimer;
        private SynchronizationContext? _ui;
        private PreferencesDoc? _preferencesBase;
        private bool _preferencesBaseRead;

        /// <summary>Levé (sur le fil de l'interface) quand le serveur a apporté des changements venus d'ailleurs, déjà appliqués aux réglages :
        /// la fenêtre doit remettre à jour favoris et couleurs des sons et reconstruire ses sections.</summary>
        public event Action? PreferencesApplied;

        /// <summary>Appelé quand les réglages viennent d'être lus (sur le fil de l'interface) : retient ce fil et lance la relève périodique.</summary>
        private void StartPreferencesSync()
        {
            _ui = SynchronizationContext.Current;
            _preferencesTimer ??= new Timer(_ => { _ = SyncPreferencesAsync(); }, null, PreferencesPollEvery, PreferencesPollEvery);
        }

        private bool PreferencesSyncAvailable =>
            _settingsLoaded && !string.IsNullOrEmpty(Settings.DiscordSessionToken) && ServerSupports("preferences_sync");

        /// <summary>Serveur et compte auxquels l'état de départ se rapporte : changer d'un des deux le rend sans objet.</summary>
        private string PreferencesTarget => $"{BaseUrl}|{Settings.DiscordUserId}";

        private PreferencesDoc BuildPreferencesDoc() =>
            PreferencesDoc.Create(Settings.FavoriteSoundIds, Settings.SoundColors, Settings.Categories, Settings.CategoryOrder);

        /// <summary>L'état du serveur reçu la dernière fois (null à la première synchronisation, ou après un changement de serveur ou de compte).</summary>
        private PreferencesDoc? PreferencesBase
        {
            get
            {
                if (Settings.PrefsBaseTarget != PreferencesTarget) return null;
                if (!_preferencesBaseRead)
                {
                    _preferencesBase = PreferencesDoc.FromJson(Settings.PrefsBaseJson);
                    _preferencesBaseRead = true;
                }
                return _preferencesBase;
            }
        }

        /// <summary>À appeler quand les réglages viennent d'être écrits (ou après le premier chargement du catalogue, avec force) : si quelque chose
        /// a changé depuis le dernier échange, en programme un dans quelques secondes. Silencieux en cas d'échec : le prochain enregistrement, la
        /// relève périodique ou le prochain lancement réessaie.</summary>
        private void QueuePreferencesSync(bool force = false)
        {
            if (!PreferencesSyncAvailable) return;
            if (!force && BuildPreferencesDoc().SemanticEquals(PreferencesBase)) return;

            var pending = new CancellationTokenSource();
            Interlocked.Exchange(ref _preferencesPending, pending)?.Cancel();
            _ = Task.Run(async () =>
            {
                try
                {
                    await Task.Delay(PreferencesDebounce, pending.Token);
                    await SyncPreferencesAsync();
                }
                catch (OperationCanceledException)
                {
                    // Une modification plus récente a pris le relais.
                }
            });
        }

        /// <summary>Un échange avec le serveur : envoie l'état local et l'état de départ, adopte le résultat de la fusion.</summary>
        internal async Task SyncPreferencesAsync()
        {
            if (!PreferencesSyncAvailable) return;
            await _preferencesSending.WaitAsync();
            try
            {
                if (!PreferencesSyncAvailable) return;
                // Les réglages appartiennent au fil de l'interface : on les lit là-bas.
                var (sent, baseDoc, target) = await OnUiAsync(() => (BuildPreferencesDoc(), PreferencesBase, PreferencesTarget));

                using var request = CreateRequest(HttpMethod.Post, "/me/preferences/sync");
                request.Content = new StringContent(JsonSerializer.Serialize(new { @base = baseDoc, doc = sent }), Encoding.UTF8, "application/json");
                using var response = await SendAsync(request);
                if (!response.IsSuccessStatusCode) return;

                using var body = JsonDocument.Parse(await response.Content.ReadAsStringAsync());
                var merged = PreferencesDoc.FromJson(body.RootElement.GetProperty("doc").GetRawText());
                if (merged is null) return;
                await OnUiAsync(() => { ApplyPreferencesResult(sent, merged, target); return true; });
            }
            catch
            {
                // Hors connexion, serveur plus ancien ou réponse inattendue : sans conséquence, on réessaiera.
            }
            finally
            {
                _preferencesSending.Release();
            }
        }

        /// <summary>Adopte le résultat de la fusion (fil de l'interface). Si les réglages ont encore changé pendant l'échange, on ne touche à rien :
        /// l'état de départ reste l'ancien et l'échange suivant fusionnera le tout (renvoyer des modifications déjà prises en compte est sans effet).</summary>
        private void ApplyPreferencesResult(PreferencesDoc sent, PreferencesDoc merged, string target)
        {
            if (target != PreferencesTarget) return;
            var current = BuildPreferencesDoc();
            if (!current.SemanticEquals(sent)) return;

            var changed = !merged.SemanticEquals(current);
            if (changed)
            {
                Settings.FavoriteSoundIds = merged.Favorites.ToList();
                Settings.SoundColors = new Dictionary<string, string>(merged.Colors);
                Settings.Categories = merged.Categories.ToDictionary(c => c.Key, c => c.Value.ToList());
                Settings.CategoryOrder = merged.Order.ToList();
            }
            _preferencesBase = merged;
            _preferencesBaseRead = true;
            Settings.PrefsBaseJson = merged.ToJson();
            Settings.PrefsBaseTarget = target;
            SaveSettings();
            if (changed) PreferencesApplied?.Invoke();
        }

        /// <summary>Exécute du travail sur le fil de l'interface et attend son résultat (directement si on y est déjà, ou si aucun fil n'a été retenu).</summary>
        private Task<T> OnUiAsync<T>(Func<T> work)
        {
            if (_ui is null || SynchronizationContext.Current == _ui) return Task.FromResult(work());
            var done = new TaskCompletionSource<T>(TaskCreationOptions.RunContinuationsAsynchronously);
            _ui.Post(_ =>
            {
                try { done.SetResult(work()); }
                catch (Exception ex) { done.SetException(ex); }
            }, null);
            return done.Task;
        }
    }
}
