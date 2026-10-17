using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.ComponentModel;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Media.Effects;
using System.Windows.Threading;
using Microsoft.Win32;
using WaseBoard.Models;
using WaseBoard.Services;
using WaseBoard.Services.Localization;
using WaseBoard.Services.MicFx;
using WaseBoard.Windows;

namespace WaseBoard
{
    public partial class MainWindow : Window
    {
        private const string FavoritesKey = "__favorites__";

        private readonly SoundLibraryService _library = new();

        /// <summary>Vrai une fois _library.Load() exécuté. Évite qu'un contrôle initialisé en XAML
        /// (ex: Slider Value="1.0") ne sauvegarde des réglages par défaut avant leur chargement.</summary>
        private bool _settingsReady;
        private readonly AudioPlaybackService _audio = new();

        /// <summary>Lecture « dans le micro » (Paramètres > Micro en jeu) : ne tourne que pendant qu'un son y joue.</summary>
        private readonly MicFeedService _micFeed = new();

        /// <summary>Sons en cours dans le micro : le serveur n'en sait rien, le sondage d'activité ne
        /// doit donc pas éteindre leur bouton (voir PollActivityAsync).</summary>
        private readonly HashSet<string> _micPlayingIds = new();
        private GlobalHotkeyManager? _hotkeys;
        private DispatcherTimer? _activityTimer;
        private DispatcherTimer? _voiceStatusTimer;
        private DispatcherTimer? _themeTimer;
        private SystemThemeSnapshot? _lastSystemTheme;

        private Point _dragStartPoint;
        private bool _isDragging;
        private string _searchQuery = "";
        private bool _sidebarCollapsed;

        public ObservableCollection<SoundItem> Sounds { get; } = new();

        /// <summary>Membres actuellement présents dans le même salon vocal que vous, affichés dans la barre latérale.</summary>
        public ObservableCollection<UserActivity> VoiceChannelMembers { get; } = new();

        /// <summary>Toasts actuellement affichés (overlay bas-droite), alimentés par ToastService.</summary>
        public ObservableCollection<ToastViewModel> Toasts { get; } = new();

        /// <summary>Entrées (son, utilisateur) actuellement en train de jouer, pour la barre "now playing".</summary>
        public ObservableCollection<NowPlayingEntry> NowPlayingEntries { get; } = new();

        /// <summary>Une notification non bloquante affichée dans l'overlay de toasts.</summary>
        public class ToastViewModel
        {
            public string Message { get; init; } = "";
            public Brush AccentColor { get; init; } = Brushes.White;
            public string Icon { get; init; } = "ℹ";
        }

        /// <summary>Une section affichée sur la page principale (★ Favoris, une catégorie perso/partagée, ou tous les sons).</summary>
        private class SectionViewModel
        {
            public string Name { get; set; } = "";
            public string CategoryKey { get; set; } = "";
            public ObservableCollection<SoundItem> Sounds { get; set; } = new();
            public bool IsManageable { get; set; }
            public bool IsShared { get; set; }
            public bool IsExpanded { get; set; } = true;
            public bool IsEmpty => Sounds.Count == 0;
            public ObservableCollection<UserActivity> OnlineUsers { get; } = new();

            /// <summary>Icône du serveur Discord (catégories partagées uniquement), affichée à côté du nom.</summary>
            public string? IconUrl { get; set; }
            public bool HasIcon => IconUrl is not null;

            /// <summary>Vrai pour les catégories personnelles et partagées (pas Favoris/Tous les sons) : affiche les flèches ↑/↓.</summary>
            public bool IsReorderable { get; set; }

            /// <summary>Vrai pour les sections de guilde : affiche le sélecteur de tri dans l'en-tête.</summary>
            public bool IsAllSounds { get; set; }
        }

        private List<SoundLibraryService.SharedCategoryInfo> _sharedCategories = new();
        private List<SectionViewModel> _currentSections = new();

        public MainWindow()
        {
            InitializeComponent();
            Title = AppIdentity.Name;
            ChromeTitleBar.TitleText = AppIdentity.Name;
            SidebarHeaderTitle.Text = "🎛️ " + AppIdentity.Name;
            TitleText.Text = "🎛️ " + AppIdentity.Name;

            // La lecture LOCALE (aperçu uniquement) pilote IsPreviewing, distinct de IsPlaying
            // qui reflète l'activité PARTAGÉE (sondée depuis le serveur, voir PollActivityAsync).
            _audio.SoundStarted += id => Dispatcher.BeginInvoke(() => SetPreviewingState(id, true));
            _audio.SoundStopped += id => Dispatcher.BeginInvoke(() => SetPreviewingState(id, false));
            _micFeed.SoundEnded += id => Dispatcher.BeginInvoke(() =>
            {
                _micPlayingIds.Remove(id);
                var item = Sounds.FirstOrDefault(s => s.Id == id);
                if (item is not null && item.ActiveUsers.Count == 0) item.IsPlaying = false;
            });

            ToastService.Requested += OnToastRequested;
        }

        private void OnToastRequested(ToastRequest request)
        {
            Dispatcher.BeginInvoke(() =>
            {
                var accent = request.Kind switch
                {
                    ToastKind.Success => new SolidColorBrush(Color.FromRgb(0x4C, 0xAF, 0x50)),
                    ToastKind.Warning => new SolidColorBrush(Color.FromRgb(0xF5, 0xA6, 0x23)),
                    ToastKind.Error => new SolidColorBrush(Color.FromRgb(0xE8, 0x11, 0x23)),
                    _ => (Brush)TryFindResource("AccentBrush") ?? Brushes.White
                };
                var icon = request.Kind switch
                {
                    ToastKind.Success => "✓",
                    ToastKind.Warning => "⚠",
                    ToastKind.Error => "⛔",
                    _ => "ℹ"
                };

                var toast = new ToastViewModel { Message = request.Message, AccentColor = accent, Icon = icon };
                Toasts.Add(toast);

                var timer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(3.5) };
                timer.Tick += (_, _) =>
                {
                    timer.Stop();
                    Toasts.Remove(toast);
                };
                timer.Start();
            });
        }

        private void SetPreviewingState(string soundId, bool isPreviewing)
        {
            var item = Sounds.FirstOrDefault(s => s.Id == soundId);
            if (item is not null) item.IsPreviewing = isPreviewing;
        }

        private async void Window_Loaded(object sender, RoutedEventArgs e)
        {
            _library.Load();
            _settingsReady = true;
            ApplyColorScheme();
            ApplyMicModeButton();
            CleanOldClips();
            ApplyClipSettings();

            // Lien waseboard:// reçu en argument de lancement (voir App.OnStartup) — appliqué
            // AVANT le bloc d'onboarding juste en dessous, qui s'ouvrira normalement si c'est un
            // premier lancement et affichera alors les champs déjà pré-remplis.
            var deepLinkJustApplied = false;
            if (App.PendingDeepLink is not null)
                deepLinkJustApplied = await ApplyDeepLinkAsync(App.PendingDeepLink, isRuntimeTrigger: false);

            var hwndSource = HwndSource.FromHwnd(new WindowInteropHelper(this).Handle);
            hwndSource?.AddHook(DeepLinkHwndHook);


            // La connexion Discord est désormais obligatoire (OAuth2) : tant qu'elle est absente,
            // l'assistant se rouvre à CHAQUE lancement, pas seulement au premier — mais
            // directement sur l'étape Discord si les étapes Bienvenue/Serveur ont déjà été vues.
            // Couvre aussi le cas d'une session invalidée en cours de route (voir SessionInvalidated).
            var needsFirstRun = !_library.Settings.HasSeenOnboarding;
            var needsLogin = string.IsNullOrWhiteSpace(_library.Settings.DiscordSessionToken);

            // Un utilisateur qui a déjà vu l'assistant peut avoir des réglages Serveur devenus
            // invalides (jeton partagé changé côté serveur — ex. une rotation de sécurité) : sans
            // cette vérification, l'assistant sautait droit à l'étape Discord avec une adresse/un
            // jeton caducs, pour un échec confus une étape plus tard au lieu du vrai problème.
            var serverNeedsReconfig = false;
            if (!needsFirstRun && !string.IsNullOrWhiteSpace(_library.Settings.ServerUrl))
            {
                var (status, _) = await _library.GetServerStatusAsync();
                serverNeedsReconfig = status == SoundLibraryService.ServerStatusResult.Unauthorized;
            }

            if (needsFirstRun || needsLogin || serverNeedsReconfig)
            {
                // Priorité : premier lancement > lien qu'on vient d'appliquer > serveur caduc >
                // simple expiration de session — voir OnboardingWindow.DescribeReason pour le texte
                // affiché à chaque cas.
                var reason = needsFirstRun ? OnboardingReason.FreshInstall
                    : deepLinkJustApplied ? OnboardingReason.DeepLinkReconnect
                    : serverNeedsReconfig ? OnboardingReason.ServerReconfigNeeded
                    : OnboardingReason.SessionExpired;

                var onboarding = new OnboardingWindow(_library.Settings, _library, reason) { Owner = this };
                onboarding.ShowDialog();
                _library.Settings.HasSeenOnboarding = true;
                _library.SaveSettings();
            }

            _library.SessionInvalidated += OnSessionInvalidated;
            _library.PreferencesApplied += OnPreferencesApplied;

            _hotkeys = new GlobalHotkeyManager(this);
            ApplyTheme();

            // RefreshVoiceStatusAsync ne touche que la barre latérale (statut vocal) et ne dépend
            // de rien ici : lancé en parallèle plutôt qu'après les deux autres, qui eux doivent
            // rester séquentiels (RefreshSharedCategoriesAsync lit Sounds, rempli par
            // RefreshCatalogAsync). Évite d'ajouter un troisième aller-retour réseau plein au
            // démarrage alors qu'il peut se faire en même temps que les deux premiers.
            var voiceStatusTask = RefreshVoiceStatusAsync();
            await RefreshCatalogAsync();
            await RefreshSharedCategoriesAsync();
            await voiceStatusTask;

            // 300ms : sur son PROPRE clic, l'allumage est optimiste et l'extinction dépend de la
            // durée réelle du son (voir PlayAndPollAsync/ScheduleLocalStopAsync), donc ni l'un ni
            // l'autre n'attend ce minuteur. Il ne sert plus qu'à refléter l'activité des AUTRES
            // utilisateurs (avatar, qui joue quoi) — pas assez sensible au timing pour justifier un
            // intervalle plus court, qui ne faisait qu'alourdir le fil UI (sondage réseau + mise à
            // jour des liaisons à chaque tick) sans bénéfice perçu.
            _activityTimer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(300) };
            _activityTimer.Tick += async (_, _) => await PollActivityAsync();
            _activityTimer.Start();

            // Statut vocal : sondé séparément, moins souvent (3s suffisent, ce n'est pas aussi
            // sensible au timing que le highlight des sons).
            _voiceStatusTimer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(3) };
            _voiceStatusTimer.Tick += async (_, _) => await RefreshVoiceStatusAsync();
            _voiceStatusTimer.Start();

            // Thème système : sondage registre plutôt que SystemEvents, cohérent avec les deux
            // timers ci-dessus. 2s suffit largement pour un changement de thème/accent Windows.
            _themeTimer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(2) };
            _themeTimer.Tick += (_, _) => ApplyColorSchemeIfSystemChanged();
            _themeTimer.Start();

            _ = CheckForUpdateOnStartupAsync();
            WarnIfMicEffectNeedsRepair();

            // Micro en jeu : vérifie en mode jeu que l'effet tourne bien sur les micros qu'on écoute.
            _micDiagTimer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(5) };
            _micDiagTimer.Tick += (_, _) => CheckMicEffectActivity();
            _micDiagTimer.Start();
        }

        private DispatcherTimer? _micDiagTimer;
        private readonly Dictionary<string, int> _micMissingStreak = new();
        private readonly HashSet<string> _micMissingWarned = new();

        /// <summary>En mode jeu, prévient (une fois par micro et par session) si une application écoute un
        /// micro équipé sans que l'effet s'y active : les sons n'y passent pas (micro incompatible, ou
        /// application en mode exclusif). Au 2e constat d'affilée seulement, pour ignorer le démarrage d'un flux.</summary>
        private void CheckMicEffectActivity()
        {
            if (!IsGameMode) return;
            var state = MicFxSetup.ReadState();
            if (!state.AnyEquipped || state.NeedsUpdate) return;

            foreach (var mic in MicFxDiagnostics.Probe(state.Microphones))
            {
                var streak = mic.EffectMissing ? _micMissingStreak.GetValueOrDefault(mic.EndpointGuid) + 1 : 0;
                _micMissingStreak[mic.EndpointGuid] = streak;
                if (streak == 2 && _micMissingWarned.Add(mic.EndpointGuid))
                    ToastService.Show(Loc.T("mic.warn.effect_inactive", mic.Name), ToastKind.Warning);
            }
        }

        /// <summary>Une mise à jour du pilote du micro efface souvent sa liste d'effets (le nôtre avec) :
        /// on le signale au démarrage plutôt que de laisser les sons disparaître du micro sans explication.</summary>
        private void WarnIfMicEffectNeedsRepair()
        {
            if (!_library.Settings.MicFeatureEnabled) return;
            var state = MicFxSetup.ReadState();
            if (state.AnyNeedsRepair)
                ToastService.Show(Loc.T("mic.warn.repair"), ToastKind.Warning);
            else if (state.NeedsUpdate)
                ToastService.Show(Loc.T("mic.warn.update"), ToastKind.Warning);
            else if (!state.AnyEquipped)
                ToastService.Show(Loc.T("mic.warn.not_installed"), ToastKind.Warning);
        }

        /// <summary>Vérification passive, non bloquante : affiche un toast si une nouvelle version
        /// est disponible. Le bouton manuel équivalent est dans Paramètres > Mises à jour.</summary>
        private async Task CheckForUpdateOnStartupAsync()
        {
            var currentVersion = System.Reflection.Assembly.GetExecutingAssembly().GetName().Version ?? new Version(0, 0, 0);
            var result = await UpdateCheckService.CheckForUpdateAsync(currentVersion);
            if (result.Available)
                ToastService.Show(Loc.T("update.available", result.LatestVersion ?? ""), ToastKind.Info);
        }

        /// <summary>Le serveur a apporté des changements de favoris, de couleurs ou de catégories faits ailleurs (page web, Activité, autre PC) :
        /// ils sont déjà dans les réglages ; on remet à jour les sons affichés et on reconstruit les sections.</summary>
        private void OnPreferencesApplied()
        {
            var favorites = new HashSet<string>(_library.Settings.FavoriteSoundIds);
            foreach (var item in Sounds)
            {
                item.IsFavorite = favorites.Contains(item.Id);
                item.ColorHex = _library.Settings.SoundColors.TryGetValue(item.Id, out var color) ? color : null;
            }
            RefreshSections();
        }

        private bool _reloginPromptShowing;

        /// <summary>Déclenché une seule fois par invalidation réelle de session (voir
        /// SoundLibraryService.SendAsync) — ouvre Paramètres directement sur la page Discord
        /// pour reconnecter, en évitant les invites en double si plusieurs appels échouent
        /// avant que l'utilisateur ait eu le temps de réagir.</summary>
        private void OnSessionInvalidated()
        {
            if (_reloginPromptShowing) return;
            _reloginPromptShowing = true;
            Dispatcher.BeginInvoke(() =>
            {
                try
                {
                    ToastService.Show(Loc.T("discord.session_expired"), ToastKind.Warning);
                    var window = new SettingsWindow(_library.Settings, _library, initialPage: "Discord") { Owner = this };
                    window.ShowDialog();
                }
                finally
                {
                    _reloginPromptShowing = false;
                }
            });
        }
    }
}
