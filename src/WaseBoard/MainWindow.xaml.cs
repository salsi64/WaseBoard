using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.ComponentModel;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Media.Effects;
using System.Windows.Threading;
using Microsoft.Win32;
using WaseBoard.Models;
using WaseBoard.Services;
using WaseBoard.Windows;

namespace WaseBoard
{
    public partial class MainWindow : Window
    {
        private const string FavoritesKey = "__favorites__";
        private const string AllSoundsKey = "__all_sounds__";

        private readonly SoundLibraryService _library = new();

        /// <summary>
        /// Devient vrai une fois _library.Load() exécuté. Sans ce garde-fou, un contrôle dont la
        /// valeur initiale est écrite en XAML (ex: Slider Value="1.0") déclenche son événement
        /// *Changed pendant InitializeComponent(), donc AVANT Load() — un handler qui enregistre
        /// immédiatement (SaveSettings) écrase alors settings.json avec des réglages par défaut
        /// vides. C'est ce qui a effacé le jeton serveur, l'ID Discord et les catégories : le
        /// slider de volume de la page principale sauvegardait à chaque lancement, avant même que
        /// les vrais réglages aient été lus depuis le disque.
        /// </summary>
        private bool _settingsReady;
        private readonly AudioPlaybackService _audio = new();
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
        }

        private List<SoundLibraryService.SharedCategoryInfo> _sharedCategories = new();
        private List<SectionViewModel> _currentSections = new();

        public MainWindow()
        {
            InitializeComponent();

            // La lecture LOCALE (aperçu uniquement) pilote IsPreviewing, distinct de IsPlaying
            // qui reflète l'activité PARTAGÉE (sondée depuis le serveur, voir PollActivityAsync).
            _audio.SoundStarted += id => Dispatcher.BeginInvoke(() => SetPreviewingState(id, true));
            _audio.SoundStopped += id => Dispatcher.BeginInvoke(() => SetPreviewingState(id, false));

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

            MainVolumeSlider.Value = _library.Settings.LocalPlaybackVolume;
            SidebarVolumeSlider.Value = _library.Settings.LocalPlaybackVolume;
            UpdateMainVolumeLabel();

            if (!_library.Settings.HasSeenOnboarding)
            {
                var onboarding = new OnboardingWindow(_library.Settings, _library) { Owner = this };
                onboarding.ShowDialog();
                // Marqué vu qu'importe le résultat (Terminer ou Passer) : sinon, tant que le jeton/l'ID
                // restent vides, l'assistant se rouvrirait à chaque lancement au lieu d'une seule fois.
                _library.Settings.HasSeenOnboarding = true;
                _library.SaveSettings();
            }

            _hotkeys = new GlobalHotkeyManager(this);
            ApplyTheme();
            await RefreshCatalogAsync();
            await RefreshSharedCategoriesAsync();
            await CacheMyUsernameAsync();
            await RefreshVoiceStatusAsync();

            // Intervalle court (300ms, au lieu d'1s auparavant) : avec un sondage plus lent, un
            // son de courte durée pouvait n'apparaître en highlight que sur un seul cycle (voire
            // aucun), avec jusqu'à 1s de retard à l'affichage — d'où l'impression que le highlight
            // se déclenchait tard et ne durait pas.
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
        }

        /// <summary>Applique la couleur de fond personnalisée (si définie) à toute l'application, immédiatement.</summary>
        private void ApplyBackgroundColor()
        {
            if (string.IsNullOrWhiteSpace(_library.Settings.BackgroundColorHex)) return;
            try
            {
                var color = (Color)ColorConverter.ConvertFromString(_library.Settings.BackgroundColorHex);
                Application.Current.Resources["BgBrush"] = new SolidColorBrush(color);
            }
            catch { /* couleur invalide enregistrée : on garde le thème par défaut */ }
        }

        /// <summary>Ne réapplique le thème système que s'il a réellement changé depuis le dernier sondage.</summary>
        private void ApplyColorSchemeIfSystemChanged()
        {
            if (!_library.Settings.FollowSystemTheme && !_library.Settings.FollowSystemAccent) return;
            var current = RegistryThemeWatcher.ReadCurrent();
            if (_lastSystemTheme is not null &&
                _lastSystemTheme.IsLightTheme == current.IsLightTheme &&
                _lastSystemTheme.AccentColor == current.AccentColor) return;
            ApplyColorScheme();
        }

        /// <summary>
        /// Applique la palette clair/sombre + accent, à chaud (brushes DynamicResource) : suit le
        /// thème/accent Windows si activé dans les Paramètres, sinon conserve le comportement
        /// historique (palette sombre fixe + couleur de fond personnalisée éventuelle).
        /// </summary>
        private void ApplyColorScheme()
        {
            var settings = _library.Settings;
            var accentFallback = Color.FromRgb(0x7C, 0x5C, 0xFF);

            if (settings.FollowSystemTheme)
            {
                var snapshot = RegistryThemeWatcher.ReadCurrent();
                _lastSystemTheme = snapshot;
                ApplyPalette(snapshot.IsLightTheme, settings.FollowSystemAccent ? snapshot.AccentColor : accentFallback);
            }
            else
            {
                ApplyPalette(isLight: false, accentFallback);
                ApplyBackgroundColor();
            }
        }

        private static void ApplyPalette(bool isLight, Color accent)
        {
            var res = Application.Current.Resources;
            if (isLight)
            {
                res["BgBrush"] = new SolidColorBrush(Color.FromRgb(0xF5, 0xF5, 0xFA));
                res["PanelBrush"] = new SolidColorBrush(Colors.White);
                res["TextBrush"] = new SolidColorBrush(Color.FromRgb(0x1E, 0x1E, 0x2E));
                res["TrackBrush"] = new SolidColorBrush(Color.FromRgb(0xE0, 0xE0, 0xEA));
            }
            else
            {
                res["BgBrush"] = new SolidColorBrush(Color.FromRgb(0x1E, 0x1E, 0x2E));
                res["PanelBrush"] = new SolidColorBrush(Color.FromRgb(0x2A, 0x2A, 0x3C));
                res["TextBrush"] = new SolidColorBrush(Color.FromRgb(0xF2, 0xF2, 0xF7));
                res["TrackBrush"] = new SolidColorBrush(Color.FromRgb(0x3A, 0x3A, 0x50));
            }
            res["AccentBrush"] = new SolidColorBrush(accent);
        }

        /// <summary>
        /// Bascule entre le thème classique (barre d'outils) et moderne (barre latérale, boutons en pilule).
        /// Le bouton Paramètres et le slider de volume de la barre d'outils (MainVolumePanel) ont
        /// chacun un équivalent dans la barre latérale (SidebarFooter) : masqués ici en thème
        /// moderne pour ne pas les afficher en double, ils restent la seule voie d'accès en
        /// thème classique puisque la barre latérale n'existe pas dans ce cas.
        /// </summary>
        private void ApplyTheme()
        {
            var isModern = _library.Settings.UiTheme == "Modern";
            ThemeState.IsModern = isModern;

            Sidebar.Visibility = isModern ? Visibility.Visible : Visibility.Collapsed;
            SidebarColumn.Width = new GridLength(isModern ? (_sidebarCollapsed ? 60 : 230) : 0);
            TitleText.Visibility = isModern ? Visibility.Collapsed : Visibility.Visible;
            SettingsButton.Visibility = isModern ? Visibility.Collapsed : Visibility.Visible;
            MainVolumePanel.Visibility = isModern ? Visibility.Collapsed : Visibility.Visible;

            // Les boutons de son utilisent un ItemTemplateSelector qui lit ThemeState.IsModern :
            // il faut reconstruire les sections pour que le changement de gabarit soit pris en compte.
            RefreshSections();
        }

        /// <summary>Résout votre pseudo/avatar Discord une fois au démarrage (mise en cache locale, pas un indicateur de présence).</summary>
        private async Task CacheMyUsernameAsync()
        {
            if (string.IsNullOrEmpty(_library.Settings.DiscordUserId)) return;

            var (found, username, avatarUrl, _, _) = await _library.VerifyUserIdAsync(_library.Settings.DiscordUserId);
            if (found)
            {
                _library.Settings.DiscordUsername = username;
                _library.Settings.DiscordAvatarUrl = avatarUrl;
                _library.SaveSettings();
            }
        }

        /// <summary>
        /// Statut vocal RÉEL, sondé en continu : contrairement à l'ancienne version (résolue une
        /// fois au démarrage à partir de la simple appartenance à un serveur Discord), celui-ci
        /// reflète votre présence vocale actuelle — jamais "connecté" si vous avez quitté le vocal,
        /// jamais le mauvais serveur si vous êtes ailleurs.
        /// </summary>
        private async Task RefreshVoiceStatusAsync()
        {
            if (string.IsNullOrEmpty(_library.Settings.DiscordUserId))
            {
                SidebarConnectionText.Text = "🔴 Identité Discord non configurée";
                VoiceChannelMembers.Clear();
                return;
            }

            var (connected, channel, guildName, channelMembers, error) = await _library.GetLiveVoiceStatusAsync();
            var who = _library.Settings.DiscordUsername ?? "vous";

            if (error is not null)
            {
                SidebarConnectionText.Text = $"⚠️ Statut indisponible : {error}";
            }
            else
            {
                SidebarConnectionText.Text = connected
                    ? $"🟢 {who} — en vocal sur {guildName}\n({channel})"
                    : $"⚪ {who} — le bot n'est pas dans votre salon vocal";
            }

            var currentIds = VoiceChannelMembers.Select(m => m.UserId).ToHashSet();
            var newIds = channelMembers.Select(m => m.UserId).ToHashSet();

            foreach (var stale in VoiceChannelMembers.Where(m => !newIds.Contains(m.UserId)).ToList())
                VoiceChannelMembers.Remove(stale);
            foreach (var fresh in channelMembers.Where(m => !currentIds.Contains(m.UserId)))
                VoiceChannelMembers.Add(fresh);
        }

        private async void JoinVoiceButton_Click(object sender, RoutedEventArgs e)
        {
            JoinVoiceButton.IsEnabled = false;
            var (success, guildName, channelName, error) = await _library.JoinMyChannelAsync();
            JoinVoiceButton.IsEnabled = true;

            if (success)
            {
                await RefreshVoiceStatusAsync();
                ToastService.Show($"🔊 Connecté à « {channelName} » sur {guildName}.", ToastKind.Success);
            }
            else
            {
                ToastService.Show(error ?? "Échec de la connexion au salon vocal.", ToastKind.Warning);
            }
        }

        // ---------- Navigation (barre latérale, thème moderne) ----------

        private class NavItem
        {
            public string Label { get; set; } = "";
            public string? IconUrl { get; set; }
            public bool HasIcon => !string.IsNullOrEmpty(IconUrl);
            public int Index { get; set; }
            public string CategoryKey { get; set; } = "";
            public bool IsReorderable { get; set; }
        }

        private void NavHome_Click(object sender, RoutedEventArgs e) => SectionsScrollViewer.ScrollToTop();

        private void NavItem_Click(object sender, RoutedEventArgs e)
        {
            if (sender is Button { Tag: NavItem navItem }) ScrollToSectionIndex(navItem.Index);
        }

        private Point _navDragStart;
        private bool _isDraggingNav;
        private Point _sectionDragStart;
        private bool _isDraggingSection;

        private void NavItem_PreviewMouseLeftButtonDown(object sender, MouseButtonEventArgs e)
        {
            _navDragStart = e.GetPosition(null);
            _isDraggingNav = false;
        }

        private void NavItem_PreviewMouseMove(object sender, MouseEventArgs e)
        {
            if (e.LeftButton != MouseButtonState.Pressed || _isDraggingNav) return;
            if (sender is not Button { Tag: NavItem navItem } button) return;
            if (!navItem.IsReorderable) return; // seules les catégories personnelles se réordonnent ainsi

            var diff = _navDragStart - e.GetPosition(null);
            if (Math.Abs(diff.X) < SystemParameters.MinimumHorizontalDragDistance &&
                Math.Abs(diff.Y) < SystemParameters.MinimumVerticalDragDistance)
                return;

            _isDraggingNav = true;
            DragDrop.DoDragDrop(button, navItem, DragDropEffects.Move);
            _isDraggingNav = false;
        }

        private void NavItem_Drop(object sender, DragEventArgs e)
        {
            if (sender is not Button { Tag: NavItem targetNav }) return;
            if (e.Data.GetData(typeof(NavItem)) is not NavItem draggedNav) return;
            if (!draggedNav.IsReorderable || !targetNav.IsReorderable) return;
            if (ReferenceEquals(draggedNav, targetNav)) return;

            var order = _library.GetCategoriesInOrder(_sharedCategories.Select(s => s.GuildId));
            var oldIndex = order.IndexOf(draggedNav.CategoryKey);
            var newIndex = order.IndexOf(targetNav.CategoryKey);
            if (oldIndex < 0 || newIndex < 0) return;

            order.RemoveAt(oldIndex);
            order.Insert(newIndex, draggedNav.CategoryKey);
            _library.SaveCategoryOrder(order);
            RefreshSections();
            e.Handled = true;
        }

        private void ScrollToSectionIndex(int index)
        {
            if (index < 0 || index >= _currentSections.Count) return;
            SectionsItemsControl.UpdateLayout();
            if (SectionsItemsControl.ItemContainerGenerator.ContainerFromIndex(index) is FrameworkElement element)
                element.BringIntoView();
        }

        /// <summary>Réduit/agrandit la barre latérale pour gagner de la place, sans perdre l'accès à la navigation (le bouton de bascule reste visible).</summary>
        private void SidebarToggle_Click(object sender, RoutedEventArgs e)
        {
            _sidebarCollapsed = !_sidebarCollapsed;

            SidebarColumn.Width = new GridLength(_sidebarCollapsed ? 60 : 230);
            SidebarToggleButton.Content = _sidebarCollapsed ? "▶" : "◀";

            var visibility = _sidebarCollapsed ? Visibility.Collapsed : Visibility.Visible;
            SidebarHeaderText.Visibility = visibility;
            SidebarHomeButton.Visibility = visibility;
            SidebarNavScroll.Visibility = visibility;
            SidebarFooter.Visibility = visibility;
        }

        private async Task RefreshCatalogAsync()
        {
            ServerStatusText.Text = "Connexion...";
            SetStatusDot(Color.FromRgb(0xF5, 0xA6, 0x23), pulsing: true);
            _hotkeys?.UnregisterAll();

            var items = await _library.FetchCatalogAsync();

            Sounds.Clear();
            foreach (var item in items) Sounds.Add(item);
            RegisterAllHotkeys();
            RefreshSections();

            // L'adresse du serveur n'est plus affichée en clair sur la page principale (visible
            // au survol via l'infobulle, pour le débogage) : seul l'état connecté/non est indiqué.
            var connected = items.Count > 0 || string.IsNullOrEmpty(_library.LastErrorDetail);
            ServerStatusText.Text = connected ? $"Connecté — {items.Count} son(s)" : "Serveur injoignable";
            ServerStatusText.ToolTip = connected
                ? _library.Settings.ServerUrl
                : $"{_library.Settings.ServerUrl}\n{_library.LastErrorDetail}";
            SetStatusDot(connected ? Color.FromRgb(0x4C, 0xAF, 0x50) : Color.FromRgb(0xE8, 0x11, 0x23), pulsing: false);

            _ = _library.PrefetchAllAsync(items);
            _ = PrecomputeWaveformsAsync(items);
        }

        /// <summary>
        /// Calcule en arrière-plan (batch limité à 4 en parallèle) les mini-waveforms manquantes
        /// pour le bouton-son classique, avec cache disque (AudioTrimService.GetOrComputeMiniWaveform)
        /// — un redémarrage n'a donc pas à tout rescanner. Ne bloque jamais l'UI : chaque son mis à
        /// jour se propage individuellement via SoundItem.WaveformPeaks (INotifyPropertyChanged).
        /// </summary>
        private async Task PrecomputeWaveformsAsync(List<SoundItem> items)
        {
            using var throttle = new SemaphoreSlim(4);
            var tasks = items.Select(async item =>
            {
                if (item.WaveformPeaks is not null) return;
                await throttle.WaitAsync();
                try
                {
                    var localPath = await _library.GetOrDownloadCachedFileAsync(item);
                    if (localPath is null) return;
                    var peaks = await Task.Run(() =>
                        AudioTrimService.GetOrComputeMiniWaveform(item.Id, localPath, _library.CacheFolder));
                    if (peaks is not null) item.WaveformPeaks = peaks;
                }
                finally { throttle.Release(); }
            });
            await Task.WhenAll(tasks);
        }

        /// <summary>
        /// Partagé par les deux sliders de volume (MainVolumeSlider en thème classique,
        /// SidebarVolumeSlider en thème moderne — voir ApplyTheme()) : un seul des deux est
        /// visible à la fois, mais on garde l'autre à jour pour retrouver la bonne valeur si le
        /// thème change en cours de session.
        /// </summary>
        private void VolumeSlider_ValueChanged(object sender, RoutedPropertyChangedEventArgs<double> e)
        {
            if (!_settingsReady) return; // évite d'écraser settings.json pendant InitializeComponent(), voir _settingsReady
            _library.Settings.LocalPlaybackVolume = (float)e.NewValue;
            _library.SaveSettings();

            if (!ReferenceEquals(sender, MainVolumeSlider)) MainVolumeSlider.Value = e.NewValue;
            if (!ReferenceEquals(sender, SidebarVolumeSlider)) SidebarVolumeSlider.Value = e.NewValue;

            UpdateMainVolumeLabel();
        }

        private void UpdateMainVolumeLabel()
        {
            var text = $"{(int)(MainVolumeSlider.Value * 100)}%";
            if (MainVolumeLabel is not null) MainVolumeLabel.Text = text;
            if (SidebarVolumeLabel is not null) SidebarVolumeLabel.Text = text;
        }

        /// <summary>Pastille de statut serveur : pulse pendant la connexion, fixe une fois résolue.</summary>
        private void SetStatusDot(Color color, bool pulsing)
        {
            StatusDot.Fill = new SolidColorBrush(color);
            var pulse = (Storyboard)StatusDot.Resources["StatusDotPulse"];
            if (pulsing)
            {
                pulse.Begin(StatusDot, true);
            }
            else
            {
                pulse.Stop(StatusDot);
                StatusDot.Opacity = 1.0;
            }
        }

        /// <summary>Sondage régulier de l'activité partagée (qui joue quoi) et de la présence (qui a l'app ouverte), communs à tous les clients.</summary>
        private async Task PollActivityAsync()
        {
            var (activity, online) = await _library.GetActivityAsync();

            foreach (var item in Sounds)
            {
                if (activity.TryGetValue(item.Id, out var users))
                {
                    item.IsPlaying = true;

                    var currentIds = item.ActiveUsers.Select(u => u.UserId).ToHashSet();
                    var newIds = users.Select(u => u.UserId).ToHashSet();

                    foreach (var stale in item.ActiveUsers.Where(u => !newIds.Contains(u.UserId)).ToList())
                        item.ActiveUsers.Remove(stale);
                    foreach (var fresh in users.Where(u => !currentIds.Contains(u.UserId)))
                        item.ActiveUsers.Add(fresh);
                }
                else
                {
                    item.IsPlaying = false;
                    if (item.ActiveUsers.Count > 0) item.ActiveUsers.Clear();
                }
            }

            // Barre "now playing" : liste plate (son, utilisateur), dérivée de la même activité et
            // réconciliée par clé (même technique anti-flicker qu'au-dessus) plutôt que reconstruite.
            var freshEntries = activity
                .SelectMany(kv => kv.Value.Select(u => (SoundId: kv.Key, User: u)))
                .Select(t =>
                {
                    var sound = Sounds.FirstOrDefault(s => s.Id == t.SoundId);
                    return new NowPlayingEntry
                    {
                        SoundId = t.SoundId,
                        UserId = t.User.UserId,
                        SoundName = sound?.Name ?? "?",
                        Emoji = sound?.Emoji,
                        Username = t.User.Username,
                        AvatarUrl = t.User.AvatarUrl
                    };
                })
                .ToList();
            var freshEntryKeys = freshEntries.Select(x => x.Key).ToHashSet();
            var currentEntryKeys = NowPlayingEntries.Select(x => x.Key).ToHashSet();
            foreach (var stale in NowPlayingEntries.Where(x => !freshEntryKeys.Contains(x.Key)).ToList())
                NowPlayingEntries.Remove(stale);
            foreach (var fresh in freshEntries.Where(x => !currentEntryKeys.Contains(x.Key)))
                NowPlayingEntries.Add(fresh);

            // Mise à jour de la barre "en ligne" des catégories partagées, sans reconstruire toute
            // l'interface (juste les collections des sections déjà affichées) pour rester fluide.
            var onlineIds = online.Select(u => u.UserId).ToHashSet();
            foreach (var section in _currentSections.Where(s => s.IsShared))
            {
                var currentIds = section.OnlineUsers.Select(u => u.UserId).ToHashSet();
                foreach (var stale in section.OnlineUsers.Where(u => !onlineIds.Contains(u.UserId)).ToList())
                    section.OnlineUsers.Remove(stale);
                foreach (var fresh in online.Where(u => !currentIds.Contains(u.UserId)))
                    section.OnlineUsers.Add(fresh);
            }

            // Met en valeur, dans la barre latérale, l'avatar de qui que ce soit du salon vocal
            // actuellement en train de jouer un son (même logique d'activité que les boutons).
            var activeUserIds = activity.Values.SelectMany(list => list.Select(u => u.UserId)).ToHashSet();
            foreach (var member in VoiceChannelMembers)
                member.IsActive = activeUserIds.Contains(member.UserId);
        }

        /// <summary>Récupère les catégories partagées par votre serveur Discord, et rafraîchit l'affichage.</summary>
        private async Task RefreshSharedCategoriesAsync()
        {
            _sharedCategories = await _library.FetchSharedCategoriesAsync();
            RefreshSections();
        }

        /// <summary>Reconstruit les sections affichées (★ Favoris, catégories perso/partagées, Tous les sons), en appliquant la recherche en cours.</summary>
        private void RefreshSections()
        {
            IEnumerable<SoundItem> Filtered(IEnumerable<SoundItem> src) =>
                string.IsNullOrWhiteSpace(_searchQuery)
                    ? src
                    : src.Where(s => s.Name.Contains(_searchQuery, StringComparison.OrdinalIgnoreCase));

            var sections = new List<SectionViewModel>
            {
                new SectionViewModel
                {
                    Name = "★ Favoris",
                    CategoryKey = FavoritesKey,
                    Sounds = new ObservableCollection<SoundItem>(Filtered(Sounds.Where(s => s.IsFavorite))),
                    IsManageable = false,
                    IsExpanded = true // toujours ouverte
                }
            };

            // Ordre unifié (personnelles + partagées) : permet de réordonner les catégories
            // partagées au même titre que les personnelles, avec les mêmes flèches ↑/↓.
            foreach (var key in _library.GetCategoriesInOrder(_sharedCategories.Select(s => s.GuildId)))
            {
                if (_library.Settings.Categories.ContainsKey(key))
                {
                    sections.Add(new SectionViewModel
                    {
                        Name = key,
                        CategoryKey = key,
                        Sounds = new ObservableCollection<SoundItem>(Filtered(_library.GetSoundsInCategory(key, Sounds))),
                        IsManageable = true,
                        IsReorderable = true,
                        IsExpanded = !_library.Settings.CollapsedSections.Contains(key)
                    });
                    continue;
                }

                var shared = _sharedCategories.FirstOrDefault(s => s.GuildId == key);
                if (shared is null) continue;

                var idSet = new HashSet<string>(shared.SoundIds);
                sections.Add(new SectionViewModel
                {
                    Name = "🌐 " + shared.GuildName,
                    CategoryKey = shared.GuildId,
                    Sounds = new ObservableCollection<SoundItem>(Filtered(Sounds.Where(s => idSet.Contains(s.Id)))),
                    IsManageable = false, // catégorie automatique : pas de renommage/suppression manuel
                    IsShared = true,
                    IsReorderable = true,
                    IconUrl = shared.IconUrl,
                    IsExpanded = !_library.Settings.CollapsedSections.Contains(shared.GuildId)
                });
            }

            sections.Add(new SectionViewModel
            {
                Name = "Tous les sons",
                CategoryKey = AllSoundsKey,
                Sounds = new ObservableCollection<SoundItem>(Filtered(Sounds)),
                IsManageable = false,
                IsExpanded = true // toujours ouverte
            });

            _currentSections = sections;
            SectionsItemsControl.ItemsSource = sections;

            // Navigation de la barre latérale : une entrée par section réellement affichée. Pour
            // les catégories partagées, l'icône du serveur Discord remplace l'emoji 🌐 générique.
            SidebarNavItemsControl.ItemsSource = sections
                .Select((s, i) =>
                {
                    var iconUrl = s.IsShared ? _sharedCategories.FirstOrDefault(sc => sc.GuildId == s.CategoryKey)?.IconUrl : null;
                    return new NavItem
                    {
                        Label = iconUrl is not null ? StripSharedPrefix(s.Name) : s.Name,
                        IconUrl = iconUrl,
                        Index = i,
                        CategoryKey = s.CategoryKey,
                        IsReorderable = s.IsReorderable
                    };
                })
                .ToList();
        }

        private void SearchBox_TextChanged(object sender, TextChangedEventArgs e)
        {
            _searchQuery = SearchBox.Text;
            RefreshSections();
        }

        private async void RefreshButton_Click(object sender, RoutedEventArgs e) => await RefreshCatalogAsync();

        private void Window_Closing(object sender, CancelEventArgs e)
        {
            _activityTimer?.Stop();
            _voiceStatusTimer?.Stop();
            _themeTimer?.Stop();
            _hotkeys?.Dispose();
            _audio.Dispose();
            ToastService.Requested -= OnToastRequested;
        }

        // ---------- Ajout / gestion des sons ----------

        private static readonly string[] SupportedExtensions = { ".mp3", ".wav", ".ogg", ".flac", ".m4a", ".wma" };

        private async void AddSoundButton_Click(object sender, RoutedEventArgs e)
        {
            var dialog = new OpenFileDialog
            {
                Title = "Choisir un fichier audio",
                Filter = "Fichiers audio (*.mp3;*.wav;*.ogg;*.flac)|*.mp3;*.wav;*.ogg;*.flac|Tous les fichiers|*.*",
                Multiselect = true
            };

            if (dialog.ShowDialog(this) != true) return;

            await AddSoundFiles(dialog.FileNames);
        }

        private async Task AddSoundFiles(IEnumerable<string> filePaths)
        {
            var rejected = new List<string>();

            foreach (var file in filePaths)
            {
                var extension = Path.GetExtension(file);
                if (!SupportedExtensions.Contains(extension, StringComparer.OrdinalIgnoreCase))
                {
                    rejected.Add(Path.GetFileName(file));
                    continue;
                }

                var suggestedName = Path.GetFileNameWithoutExtension(file);
                var trimWindow = new TrimWindow(file, suggestedName) { Owner = this };

                if (trimWindow.ShowDialog() != true || trimWindow.ResultFilePath is null)
                    continue;

                void CleanupTempFile()
                {
                    if (trimWindow.ResultIsTemporaryFile && File.Exists(trimWindow.ResultFilePath))
                    {
                        try { File.Delete(trimWindow.ResultFilePath); } catch { /* fichier temporaire, non bloquant */ }
                    }
                }

                // Détection de doublons : hash du fichier final (après découpe) contre celui de
                // chaque son déjà présent dans le catalogue, avant même d'envoyer quoi que ce
                // soit au serveur. À défaut de correspondance exacte de contenu, un même nom
                // (catalogue partagé entre tous les utilisateurs d'un serveur) reste un signal
                // utile, plus faible, qu'on remonte différemment.
                var contentHash = SoundLibraryService.ComputeFileHash(trimWindow.ResultFilePath);
                var hashMatch = Sounds.FirstOrDefault(s => s.ContentHash is not null && s.ContentHash == contentHash);
                var nameMatch = hashMatch is null
                    ? Sounds.FirstOrDefault(s => string.Equals(s.Name, trimWindow.ResultName, StringComparison.OrdinalIgnoreCase))
                    : null;

                if (hashMatch is not null)
                {
                    var proceed = ConfirmDialog.Show(this,
                        $"Ce fichier est identique à « {hashMatch.Name} », déjà présent dans le catalogue. L'ajouter quand même ?");
                    if (!proceed) { CleanupTempFile(); continue; }
                }
                else if (nameMatch is not null)
                {
                    var proceed = ConfirmDialog.Show(this,
                        $"Un son nommé « {nameMatch.Name} » existe déjà (contenu différent). L'ajouter quand même ?");
                    if (!proceed) { CleanupTempFile(); continue; }
                }

                var uploaded = await _library.UploadSoundAsync(trimWindow.ResultFilePath, trimWindow.ResultName);
                CleanupTempFile();

                if (uploaded is not null)
                {
                    Sounds.Add(uploaded);
                    RegisterHotkeyFor(uploaded);
                }
                else
                {
                    ToastService.Show(
                        $"Impossible d'envoyer « {trimWindow.ResultName} » au serveur." +
                        (string.IsNullOrEmpty(_library.LastErrorDetail) ? "" : " " + _library.LastErrorDetail),
                        ToastKind.Error);
                }
            }

            RefreshSections();

            if (rejected.Count > 0)
            {
                ToastService.Show(
                    "Fichier(s) ignoré(s), format non supporté : " + string.Join(", ", rejected),
                    ToastKind.Warning);
            }
        }

        private void SoundButton_Click(object sender, RoutedEventArgs e)
        {
            if (sender is Button { Tag: SoundItem item } button)
            {
                AnimationHelpers.PlayBounce(button);
                Play(item);
            }
        }

        private void PreviewIcon_MouseLeftButtonDown(object sender, MouseButtonEventArgs e)
        {
            e.Handled = true; // empêche le clic principal du bouton parent de se déclencher aussi
            if (sender is FrameworkElement { DataContext: SoundItem item })
                PreviewLocal(item);
        }

        private void SoundButton_RightClick(object sender, MouseButtonEventArgs e)
        {
            if (sender is not Button { Tag: SoundItem item } button) return;

            var menu = new ContextMenu();

            var favoriteItem = new MenuItem { Header = item.IsFavorite ? "★ Retirer des favoris" : "☆ Ajouter aux favoris" };
            favoriteItem.Click += (_, _) => { _library.ToggleFavorite(item); RefreshSections(); };

            var categoryMenu = new MenuItem { Header = "Ajouter à une catégorie" };
            foreach (var categoryName in _library.GetCategoriesInOrder())
            {
                var catItem = new MenuItem { Header = categoryName };
                catItem.Click += (_, _) => { _library.AddSoundToCategory(categoryName, item); RefreshSections(); };
                categoryMenu.Items.Add(catItem);
            }
            foreach (var shared in _sharedCategories)
            {
                var catItem = new MenuItem { Header = "🌐 " + shared.GuildName };
                catItem.Click += async (_, _) =>
                {
                    await _library.SetSharedCategorySoundAsync(shared.GuildId, item, true);
                    await RefreshSharedCategoriesAsync();
                };
                categoryMenu.Items.Add(catItem);
            }
            categoryMenu.Items.Add(new Separator());
            var newCategoryItem = new MenuItem { Header = "Nouvelle catégorie..." };
            newCategoryItem.Click += (_, _) =>
            {
                var name = PromptDialog.Show(this, "Nom de la nouvelle catégorie :", "");
                if (string.IsNullOrWhiteSpace(name)) return;
                _library.CreateCategory(name.Trim());
                _library.AddSoundToCategory(name.Trim(), item);
                RefreshSections();
            };
            categoryMenu.Items.Add(newCategoryItem);

            var emojiItem = new MenuItem { Header = string.IsNullOrEmpty(item.Emoji) ? "Choisir un emoji..." : $"Modifier l'emoji ({item.Emoji})" };
            emojiItem.Click += (_, _) =>
            {
                var picker = new EmojiPickerWindow(item.Emoji) { Owner = this };
                if (picker.ShowDialog() == true && picker.Result is not null)
                {
                    _library.SetEmoji(item, picker.Result);
                    RefreshSections();
                }
            };

            var removeEmojiItem = new MenuItem { Header = "Retirer l'emoji", IsEnabled = !string.IsNullOrEmpty(item.Emoji) };
            removeEmojiItem.Click += (_, _) => { _library.SetEmoji(item, null); RefreshSections(); };

            var renameItem = new MenuItem { Header = "Renommer" };
            renameItem.Click += async (_, _) =>
            {
                var newName = PromptDialog.Show(this, "Nouveau nom :", item.Name);
                if (string.IsNullOrWhiteSpace(newName)) return;

                var ok = await _library.RenameSoundAsync(item, newName.Trim());
                if (ok) RefreshSections();
                else
                {
                    ToastService.Show(
                        "Renommage échoué." + (string.IsNullOrEmpty(_library.LastErrorDetail) ? "" : " " + _library.LastErrorDetail),
                        ToastKind.Warning);
                }
            };

            var hotkeyItem = new MenuItem { Header = string.IsNullOrEmpty(item.Hotkey) ? "Définir un raccourci..." : $"Modifier le raccourci ({item.Hotkey})" };
            hotkeyItem.Click += (_, _) => AssignHotkey(item);

            var removeHotkeyItem = new MenuItem { Header = "Retirer le raccourci", IsEnabled = !string.IsNullOrEmpty(item.Hotkey) };
            removeHotkeyItem.Click += (_, _) =>
            {
                _library.SetHotkey(item, null);
                ResyncHotkeys();
            };

            var deleteItem = new MenuItem { Header = "Supprimer" };
            deleteItem.Click += async (_, _) =>
            {
                var confirm = ConfirmDialog.Show(this, $"Supprimer « {item.Name} » du catalogue partagé ?");
                if (!confirm) return;

                var ok = await _library.DeleteSoundAsync(item);
                if (ok)
                {
                    // Après le retrait (pas avant) : ResyncHotkeys() reconstruit depuis Sounds, donc
                    // le raccourci de ce son ne disparaît vraiment que s'il n'est plus dans la
                    // collection au moment de l'appel — sinon il resterait actif jusqu'au prochain
                    // resync et redéclencherait Play() sur un son qui n'existe plus côté serveur.
                    Sounds.Remove(item);
                    ResyncHotkeys();
                    RefreshSections();
                }
                else ToastService.Show("Suppression échouée.", ToastKind.Warning);
            };

            menu.Items.Add(favoriteItem);
            menu.Items.Add(categoryMenu);

            // Retirer de la catégorie EXACTE d'où l'on a cliqué (pas "toutes les catégories"),
            // retrouvée en remontant l'arbre visuel jusqu'au conteneur de la section — un même
            // son étant potentiellement affiché dans plusieurs sections à la fois.
            var section = FindEnclosingSection(button);
            if (section is not null && section.CategoryKey != AllSoundsKey && section.CategoryKey != FavoritesKey)
            {
                var label = section.IsShared
                    ? $"Retirer de « {StripSharedPrefix(section.Name)} » (partagée)"
                    : $"Retirer de « {section.Name} »";

                var removeFromSectionItem = new MenuItem { Header = label };
                removeFromSectionItem.Click += async (_, _) =>
                {
                    if (section.IsShared)
                    {
                        await _library.SetSharedCategorySoundAsync(section.CategoryKey, item, false);
                        await RefreshSharedCategoriesAsync();
                    }
                    else
                    {
                        _library.RemoveSoundFromCategory(section.CategoryKey, item);
                        RefreshSections();
                    }
                };
                menu.Items.Add(removeFromSectionItem);
            }

            menu.Items.Add(emojiItem);
            menu.Items.Add(removeEmojiItem);
            menu.Items.Add(renameItem);
            menu.Items.Add(hotkeyItem);
            menu.Items.Add(removeHotkeyItem);
            menu.Items.Add(new Separator());
            menu.Items.Add(deleteItem);
            menu.IsOpen = true;
        }

        /// <summary>Retire le préfixe "🌐 " des noms de catégories partagées (ex: quand une icône Discord réelle le remplace visuellement).
        /// En chaîne plutôt qu'en char : l'emoji 🌐 occupe deux unités UTF-16 (paire de substituts) et ne tient pas dans un seul char.</summary>
        private static string StripSharedPrefix(string name) =>
            name.StartsWith("🌐 ") ? name.Substring("🌐 ".Length) : name;

        /// <summary>Retrouve la section (catégorie) qui contient physiquement ce bouton, en remontant l'arbre visuel.</summary>
        private static SectionViewModel? FindEnclosingSection(DependencyObject? element)
        {
            while (element is not null)
            {
                if (element is FrameworkElement { Tag: SectionViewModel section }) return section;
                element = VisualTreeHelper.GetParent(element);
            }
            return null;
        }

        // ---------- Réorganisation / classement par glisser-déposer ----------

        private Button? _dragOverButton;
        private Border? _dragVisual;

        private void SoundButton_PreviewMouseLeftButtonDown(object sender, MouseButtonEventArgs e)
        {
            _dragStartPoint = e.GetPosition(null);
            _isDragging = false;
        }

        private void SoundButton_PreviewMouseMove(object sender, MouseEventArgs e)
        {
            if (e.LeftButton != MouseButtonState.Pressed || _isDragging) return;
            if (sender is not Button { Tag: SoundItem item } button) return;

            var diff = _dragStartPoint - e.GetPosition(null);
            if (Math.Abs(diff.X) < SystemParameters.MinimumHorizontalDragDistance &&
                Math.Abs(diff.Y) < SystemParameters.MinimumVerticalDragDistance)
                return;

            _isDragging = true;
            StartWiggle(button);
            ShowDragVisual(button, e.GetPosition(DragVisualCanvas));
            DragDrop.DoDragDrop(button, item, DragDropEffects.Move);
            HideDragVisual();
            StopWiggle(button);
            ClearDragOverHighlight();
            _isDragging = false;
        }

        /// <summary>
        /// Copie semi-transparente du bouton, affichée sur un calque flottant et repositionnée à
        /// chaque Window_PreviewDragOver : le bouton d'origine ne fait que se balancer sur place
        /// (StartWiggle), ce qui ne montre pas que le son "voyage" jusqu'à l'endroit visé pendant
        /// qu'on maintient le clic. Ce visuel suit le curseur pour combler ce manque, le vrai
        /// classement restant appliqué au dépôt (Section_Drop / SoundButton_DragOver).
        /// </summary>
        private void ShowDragVisual(Button source, Point startPosition)
        {
            _dragVisual = new Border
            {
                Width = source.ActualWidth,
                Height = source.ActualHeight,
                Background = new VisualBrush(source) { Stretch = Stretch.None },
                Opacity = 0.8,
                IsHitTestVisible = false,
                Effect = new DropShadowEffect { Color = Colors.Black, BlurRadius = 14, ShadowDepth = 2, Opacity = 0.5 }
            };
            DragVisualCanvas.Children.Add(_dragVisual);
            PositionDragVisual(startPosition);
        }

        private void PositionDragVisual(Point position)
        {
            if (_dragVisual is null) return;
            Canvas.SetLeft(_dragVisual, position.X - _dragVisual.Width / 2);
            Canvas.SetTop(_dragVisual, position.Y - _dragVisual.Height / 2);
        }

        private void HideDragVisual()
        {
            if (_dragVisual is null) return;
            DragVisualCanvas.Children.Remove(_dragVisual);
            _dragVisual = null;
        }

        /// <summary>Fait suivre le calque flottant (ShowDragVisual) au curseur pendant tout glisser-déposer
        /// en cours dans la fenêtre — tunnel depuis la racine, donc reçu même si un contrôle enfant
        /// (bouton, section...) gère aussi son propre DragOver.</summary>
        private void Window_PreviewDragOver(object sender, DragEventArgs e)
        {
            if (_dragVisual is not null)
                PositionDragVisual(e.GetPosition(DragVisualCanvas));
        }

        /// <summary>Petit balancement (façon icônes iOS en réorganisation) sur le bouton en cours de déplacement.</summary>
        private static void StartWiggle(Button button)
        {
            var transform = new RotateTransform(0);
            button.RenderTransform = transform;
            button.RenderTransformOrigin = new Point(0.5, 0.5);

            var animation = new DoubleAnimation
            {
                From = -3,
                To = 3,
                Duration = TimeSpan.FromMilliseconds(110),
                AutoReverse = true,
                RepeatBehavior = RepeatBehavior.Forever
            };
            transform.BeginAnimation(RotateTransform.AngleProperty, animation);
        }

        private static void StopWiggle(Button button)
        {
            if (button.RenderTransform is RotateTransform transform)
                transform.BeginAnimation(RotateTransform.AngleProperty, null);
            button.RenderTransform = Transform.Identity;
        }

        /// <summary>
        /// Met en valeur le bouton survolé ET réordonne EN DIRECT pendant le survol (pas seulement
        /// au dépôt) : c'est ce qui déclenche l'animation d'AnimatedWrapPanel — les autres boutons
        /// s'écartent visiblement pendant qu'on glisse, comme sur un écran d'accueil de smartphone,
        /// plutôt qu'un simple saut brutal une fois le bouton relâché.
        /// </summary>
        private DateTime _lastLiveReorder = DateTime.MinValue;
        private static readonly TimeSpan LiveReorderThrottle = TimeSpan.FromMilliseconds(220);

        private void SoundButton_DragOver(object sender, DragEventArgs e)
        {
            if (sender is not Button { Tag: SoundItem targetItem } button) return;
            if (e.Data.GetData(typeof(SoundItem)) is not SoundItem draggedItem) return;

            if (!ReferenceEquals(_dragOverButton, button))
            {
                ClearDragOverHighlight();
                _dragOverButton = button;
                button.Effect = new DropShadowEffect { Color = Colors.White, BlurRadius = 16, ShadowDepth = 0, Opacity = 0.9 };
            }

            if (ReferenceEquals(draggedItem, targetItem)) return;

            // Espace les réordonnancements en direct d'au moins ~220ms : sans ça, le moindre
            // frôlement d'un bouton voisin pendant le survol redéclenche l'animation en boucle
            // très rapidement, ce qui donne une impression de nervosité même avec une transition
            // individuellement douce. Le highlight de la cible (ci-dessus), lui, reste instantané.
            if (DateTime.Now - _lastLiveReorder < LiveReorderThrottle) return;
            _lastLiveReorder = DateTime.Now;

            // Collection maîtresse (l'ordre qui sera persisté) : toujours réordonnée, quelle que
            // soit la section survolée, puisque c'est elle qui définit l'ordre global.
            var masterOld = Sounds.IndexOf(draggedItem);
            var masterNew = Sounds.IndexOf(targetItem);
            if (masterOld >= 0 && masterNew >= 0 && masterOld != masterNew)
                Sounds.Move(masterOld, masterNew);

            // Et la section affichée elle-même (sa propre collection filtrée), pour que
            // l'animation soit visible immédiatement sans reconstruire toute la page — seulement
            // si le son survolé est déjà membre de cette section (sinon, ce sera un classement,
            // géré au dépôt par ApplySectionMembership, pas un simple réordonnancement).
            var section = _currentSections.FirstOrDefault(s => s.Sounds.Contains(draggedItem) && s.Sounds.Contains(targetItem));
            if (section is not null)
            {
                var sectionOld = section.Sounds.IndexOf(draggedItem);
                var sectionNew = section.Sounds.IndexOf(targetItem);
                if (sectionOld >= 0 && sectionNew >= 0 && sectionOld != sectionNew)
                    section.Sounds.Move(sectionOld, sectionNew);
            }
        }

        private void SoundButton_DragLeave(object sender, DragEventArgs e)
        {
            if (sender is Button button && ReferenceEquals(_dragOverButton, button))
                ClearDragOverHighlight();
        }

        private void ClearDragOverHighlight()
        {
            // ClearValue (et non "Effect = null") : le bouton classique pilote aussi son Effect
            // via un déclencheur de style pour le highlight de lecture partagé — une simple
            // affectation à null resterait en valeur locale et bloquerait ce highlight en
            // permanence après un glisser-déposer. ClearValue laisse le déclencheur reprendre la main.
            _dragOverButton?.ClearValue(UIElement.EffectProperty);
            _dragOverButton = null;
        }

        private async void Section_Drop(object sender, DragEventArgs e)
        {
            if (sender is not FrameworkElement { Tag: SectionViewModel section }) return;
            if (e.Data.GetData(typeof(SoundItem)) is not SoundItem draggedItem) return;

            // Si le son était déjà membre de cette section, le survol a déjà tout réordonné en
            // direct (voir SoundButton_DragOver) : inutile de reconstruire, ça casserait
            // l'animation en cours. Seul un VRAI nouveau classement (favoris/catégorie qu'il ne
            // possédait pas encore) nécessite de reconstruire les sections.
            var wasAlreadyMember = section.CategoryKey == AllSoundsKey || section.Sounds.Contains(draggedItem);

            await ApplySectionMembership(section, draggedItem);
            _library.SaveSoundOrder(Sounds);
            ClearDragOverHighlight();
            e.Handled = true;

            if (!wasAlreadyMember) RefreshSections();
        }

        private async Task ApplySectionMembership(SectionViewModel section, SoundItem item)
        {
            if (section.CategoryKey == FavoritesKey)
            {
                if (!item.IsFavorite) _library.ToggleFavorite(item);
            }
            else if (section.IsShared)
            {
                await _library.SetSharedCategorySoundAsync(section.CategoryKey, item, true);
                _sharedCategories = await _library.FetchSharedCategoriesAsync();
            }
            else if (section.CategoryKey != AllSoundsKey)
            {
                _library.AddSoundToCategory(section.CategoryKey, item);
            }
        }

        // ---------- Gestion des catégories ----------

        /// <summary>
        /// Glisser-déposer d'un en-tête de section (page principale) pour réordonner les
        /// catégories personnelles ET partagées — même logique que NavItem_Drop côté sidebar.
        /// Les gestionnaires de détection (Preview*) sont posés sur l'Expander lui-même, pas sur
        /// l'en-tête imbriqué : le ToggleButton interne (bascule ouverture/fermeture) capture la
        /// souris dès le clic, ce qui coupe le tunneling des événements suivants vers un élément
        /// imbriqué à l'intérieur de lui — l'Expander, étant un ANCÊTRE du ToggleButton, continue
        /// lui de recevoir les événements malgré la capture. Piège : une fois la capture active,
        /// e.OriginalSource ne reflète plus l'élément précis sous le curseur (il "remonte" vers
        /// l'élément capturant) — la vérification "vient du header, pas du contenu" doit donc être
        /// faite UNE FOIS au clic initial (avant toute capture) et mémorisée, jamais recalculée
        /// pendant le déplacement.
        /// </summary>
        private static bool IsDescendantOf(DependencyObject? element, DependencyObject ancestor)
        {
            while (element is not null)
            {
                if (ReferenceEquals(element, ancestor)) return true;
                element = VisualTreeHelper.GetParent(element) ?? LogicalTreeHelper.GetParent(element);
            }
            return false;
        }

        private bool _sectionDragFromHeader;
        private Border? _sectionDragOverBorder;

        private void SectionHeader_PreviewMouseLeftButtonDown(object sender, MouseButtonEventArgs e)
        {
            if (sender is not Expander { Header: Border headerBorder }) return;

            _sectionDragFromHeader = IsDescendantOf(e.OriginalSource as DependencyObject, headerBorder);
            _sectionDragStart = e.GetPosition(null);
            _isDraggingSection = false;
        }

        private void SectionHeader_PreviewMouseMove(object sender, MouseEventArgs e)
        {
            if (e.LeftButton != MouseButtonState.Pressed || _isDraggingSection || !_sectionDragFromHeader) return;
            if (sender is not Expander { DataContext: SectionViewModel section, Header: Border headerBorder } expander) return;
            if (!section.IsReorderable) return;

            var diff = _sectionDragStart - e.GetPosition(null);
            if (Math.Abs(diff.X) < SystemParameters.MinimumHorizontalDragDistance &&
                Math.Abs(diff.Y) < SystemParameters.MinimumVerticalDragDistance)
                return;

            _isDraggingSection = true;
            headerBorder.Opacity = 0.5; // indique visuellement quelle section est en cours de déplacement
            DragDrop.DoDragDrop(expander, section, DragDropEffects.Move);
            headerBorder.Opacity = 1.0;
            ClearSectionDragOverHighlight();
            _isDraggingSection = false;
        }

        private void SectionHeader_DragEnter(object sender, DragEventArgs e)
        {
            if (sender is not Border border || !e.Data.GetDataPresent(typeof(SectionViewModel))) return;
            if (ReferenceEquals(_sectionDragOverBorder, border)) return;

            ClearSectionDragOverHighlight();
            _sectionDragOverBorder = border;
            border.BorderBrush = (Brush)FindResource("AccentBrush");
            border.BorderThickness = new Thickness(2);
        }

        private void SectionHeader_DragLeave(object sender, DragEventArgs e)
        {
            if (sender is Border border && ReferenceEquals(_sectionDragOverBorder, border))
                ClearSectionDragOverHighlight();
        }

        private void ClearSectionDragOverHighlight()
        {
            if (_sectionDragOverBorder is null) return;
            _sectionDragOverBorder.BorderThickness = new Thickness(0);
            _sectionDragOverBorder = null;
        }

        private void SectionHeader_Drop(object sender, DragEventArgs e)
        {
            ClearSectionDragOverHighlight();
            if (sender is not FrameworkElement { DataContext: SectionViewModel targetSection }) return;
            if (e.Data.GetData(typeof(SectionViewModel)) is not SectionViewModel draggedSection) return;
            if (!draggedSection.IsReorderable || !targetSection.IsReorderable) return;
            if (ReferenceEquals(draggedSection, targetSection)) return;

            var order = _library.GetCategoriesInOrder(_sharedCategories.Select(s => s.GuildId));
            var oldIndex = order.IndexOf(draggedSection.CategoryKey);
            var newIndex = order.IndexOf(targetSection.CategoryKey);
            if (oldIndex < 0 || newIndex < 0) return;

            order.RemoveAt(oldIndex);
            order.Insert(newIndex, draggedSection.CategoryKey);
            _library.SaveCategoryOrder(order);
            RefreshSections();
            e.Handled = true;
        }

        /// <summary>
        /// Filet de sécurité pour TOUTES les sections : le binding OneTime initial d'IsExpanded ne
        /// déclenche Section_Expanded/Collapsed que si sa valeur diffère du défaut de l'Expander
        /// (false), et le fait avant que le contrôle soit réellement chargé — ce qui peut échouer à
        /// synchroniser la hauteur visible (ExpandSiteBorder.MaxHeight) avec l'état voulu. Comme
        /// RefreshSections() recrée systématiquement tous les Expanders (nouvelle liste, pas de
        /// notification incrémentale), une catégorie que l'utilisateur avait ouverte se retrouvait
        /// ainsi visuellement repliée après un ajout de son (ou toute autre action qui rafraîchit
        /// les sections) même si IsExpanded valait bien true. On force donc ici, une fois le
        /// contrôle réellement chargé, la hauteur à correspondre à IsExpanded — sans animation,
        /// pour ne pas provoquer de "pop" au premier affichage.
        /// </summary>
        private void Section_Loaded(object sender, RoutedEventArgs e)
        {
            if (sender is not Expander expander) return;

            expander.ApplyTemplate();
            if (expander.Template?.FindName("ExpandSiteBorder", expander) is Border border)
                border.MaxHeight = expander.IsExpanded ? double.PositiveInfinity : 0;
        }

        private void Section_Expanded(object sender, RoutedEventArgs e)
        {
            if (sender is not Expander expander) return;
            if (expander.Tag is string key)
            {
                _library.Settings.CollapsedSections.Remove(key);
                _library.SaveSettings();
            }
            AnimateExpanderHeight(expander, expanding: true);
        }

        private void Section_Collapsed(object sender, RoutedEventArgs e)
        {
            if (sender is not Expander expander) return;

            // "Favoris" et "Tous les sons" restent toujours ouvertes : on annule immédiatement le
            // repli au lieu de le persister.
            if (expander.Tag is string key0 && (key0 == FavoritesKey || key0 == AllSoundsKey))
            {
                expander.IsExpanded = true;
                return;
            }

            if (expander.Tag is string key && !_library.Settings.CollapsedSections.Contains(key))
            {
                _library.Settings.CollapsedSections.Add(key);
                _library.SaveSettings();
            }
            AnimateExpanderHeight(expander, expanding: false);
        }

        /// <summary>
        /// Anime la hauteur visible du contenu d'une section (MaxHeight, 0 ↔ hauteur naturelle
        /// mesurée). Les sections sont reconstruites à chaque RefreshSections() (recherche,
        /// changement de thème...) : tant que l'Expander n'a pas encore été chargé (IsLoaded), le
        /// déclenchement vient du binding OneTime initial, pas d'un clic utilisateur — on applique
        /// alors l'état directement, sans animation, pour éviter un "pop" à chaque frappe de recherche.
        /// </summary>
        private static void AnimateExpanderHeight(Expander expander, bool expanding)
        {
            expander.ApplyTemplate();
            if (expander.Template?.FindName("ExpandSiteBorder", expander) is not Border border) return;

            if (!expander.IsLoaded)
            {
                border.BeginAnimation(FrameworkElement.MaxHeightProperty, null);
                border.MaxHeight = expanding ? double.PositiveInfinity : 0;
                return;
            }

            if (expanding)
            {
                border.BeginAnimation(FrameworkElement.MaxHeightProperty, null);
                border.MaxHeight = double.PositiveInfinity;
                border.Measure(new Size(border.ActualWidth > 0 ? border.ActualWidth : double.PositiveInfinity, double.PositiveInfinity));
                var target = border.DesiredSize.Height;
                border.MaxHeight = 0;

                var anim = new DoubleAnimation(0, target, TimeSpan.FromMilliseconds(220))
                { EasingFunction = new CubicEase { EasingMode = EasingMode.EaseOut } };
                anim.Completed += (_, _) => border.MaxHeight = double.PositiveInfinity;
                border.BeginAnimation(FrameworkElement.MaxHeightProperty, anim);
            }
            else
            {
                var current = border.ActualHeight;
                border.BeginAnimation(FrameworkElement.MaxHeightProperty, null);
                border.MaxHeight = current;

                var anim = new DoubleAnimation(current, 0, TimeSpan.FromMilliseconds(200))
                { EasingFunction = new CubicEase { EasingMode = EasingMode.EaseIn } };
                border.BeginAnimation(FrameworkElement.MaxHeightProperty, anim);
            }
        }

        /// <summary>Menu déroulant (Nouvelle catégorie / Importer) plutôt qu'un second bouton dans une
        /// barre d'outils déjà tassée sur une seule ligne.</summary>
        private void AddCategoryButton_Click(object sender, RoutedEventArgs e)
        {
            var menu = new ContextMenu { PlacementTarget = AddCategoryButton, Placement = PlacementMode.Bottom };

            var newItem = new MenuItem { Header = "Nouvelle catégorie..." };
            newItem.Click += (_, _) => CreateCategoryPrompt();
            menu.Items.Add(newItem);

            var importItem = new MenuItem { Header = "📥 Importer une catégorie..." };
            importItem.Click += async (_, _) => await ImportCategoryAsync();
            menu.Items.Add(importItem);

            menu.IsOpen = true;
        }

        private void CreateCategoryPrompt()
        {
            var name = PromptDialog.Show(this, "Nom de la nouvelle catégorie :", "");
            if (string.IsNullOrWhiteSpace(name)) return;
            _library.CreateCategory(name.Trim());
            RefreshSections();
        }

        /// <summary>Format du fichier produit par ExportCategory / lu par ImportCategoryAsync.
        /// Le nom est conservé en plus de l'id de chaque son : ça permet de retrouver un son par
        /// nom si le fichier est importé sur un autre serveur WaseBoard (catalogue différent, donc
        /// ids différents) qui possède malgré tout un son de même nom.</summary>
        private class CategoryExport
        {
            public string Category { get; set; } = "";
            public List<CategoryExportSound> Sounds { get; set; } = new();
        }

        private class CategoryExportSound
        {
            public string Id { get; set; } = "";
            public string Name { get; set; } = "";
        }

        /// <summary>Clic droit sur la barre d'une section : menu avec l'export, seule action disponible
        /// pour toutes les sections (favoris, catégorie perso/partagée, tous les sons) — contrairement
        /// à renommer/supprimer (boutons dédiés, ✎/🗑) qui restent réservés aux catégories personnelles.</summary>
        private void SectionHeader_RightClick(object sender, MouseButtonEventArgs e)
        {
            if (sender is not FrameworkElement { Tag: SectionViewModel section }) return;

            var menu = new ContextMenu();
            var exportItem = new MenuItem { Header = "⬇ Exporter cette catégorie..." };
            exportItem.Click += (_, _) => ExportCategory(section);
            menu.Items.Add(exportItem);
            menu.IsOpen = true;
        }

        /// <summary>Exporte n'importe quelle section (favoris, catégorie perso/partagée, tous les sons)
        /// dans un fichier JSON partageable — action non destructive, disponible pour toutes,
        /// contrairement à renommer/supprimer qui restent réservés aux catégories personnelles.</summary>
        private void ExportCategory(SectionViewModel section)
        {
            var dialog = new SaveFileDialog
            {
                Title = "Exporter la catégorie",
                Filter = "Catégorie WaseBoard (*.wbcat.json)|*.wbcat.json",
                FileName = SanitizeFileName(StripSharedPrefix(section.Name)) + ".wbcat.json"
            };
            if (dialog.ShowDialog(this) != true) return;

            var export = new CategoryExport
            {
                Category = StripSharedPrefix(section.Name),
                Sounds = section.Sounds.Select(s => new CategoryExportSound { Id = s.Id, Name = s.Name }).ToList()
            };

            try
            {
                File.WriteAllText(dialog.FileName, JsonSerializer.Serialize(export, new JsonSerializerOptions { WriteIndented = true }));
                ToastService.Show($"Catégorie « {export.Category} » exportée ({export.Sounds.Count} son(s)).", ToastKind.Success);
            }
            catch (Exception ex)
            {
                ToastService.Show("Échec de l'export : " + ex.Message, ToastKind.Error);
            }
        }

        private static string SanitizeFileName(string name)
        {
            var invalid = Path.GetInvalidFileNameChars();
            var cleaned = new string(name.Where(c => !invalid.Contains(c)).ToArray()).Trim();
            return string.IsNullOrEmpty(cleaned) ? "categorie" : cleaned;
        }

        /// <summary>Crée une nouvelle catégorie personnelle à partir d'un fichier exporté par
        /// ExportCategory_Click. Les sons sont retrouvés par id, puis par nom en repli (utile si le
        /// fichier vient d'un autre serveur WaseBoard, donc d'un catalogue avec d'autres ids) ;
        /// ceux introuvables sur CE serveur sont simplement ignorés et comptés dans le résumé.</summary>
        private async Task ImportCategoryAsync()
        {
            var dialog = new OpenFileDialog
            {
                Title = "Importer une catégorie",
                Filter = "Catégorie WaseBoard (*.wbcat.json)|*.wbcat.json|Tous les fichiers|*.*"
            };
            if (dialog.ShowDialog(this) != true) return;

            CategoryExport? import;
            try
            {
                var json = await File.ReadAllTextAsync(dialog.FileName);
                import = JsonSerializer.Deserialize<CategoryExport>(json);
            }
            catch (Exception ex)
            {
                ToastService.Show("Fichier de catégorie invalide : " + ex.Message, ToastKind.Error);
                return;
            }

            if (import is null || import.Sounds.Count == 0)
            {
                ToastService.Show("Ce fichier ne contient aucun son.", ToastKind.Warning);
                return;
            }

            var suggestedName = string.IsNullOrWhiteSpace(import.Category) ? "Catégorie importée" : import.Category;
            var name = PromptDialog.Show(this, "Nom de la catégorie importée :", suggestedName);
            if (string.IsNullOrWhiteSpace(name)) return;
            var trimmedName = name.Trim();

            _library.CreateCategory(trimmedName);

            var found = 0;
            foreach (var soundRef in import.Sounds)
            {
                var match = Sounds.FirstOrDefault(s => s.Id == soundRef.Id)
                            ?? Sounds.FirstOrDefault(s => string.Equals(s.Name, soundRef.Name, StringComparison.OrdinalIgnoreCase));
                if (match is null) continue;
                _library.AddSoundToCategory(trimmedName, match);
                found++;
            }

            RefreshSections();

            var skipped = import.Sounds.Count - found;
            ToastService.Show(
                skipped > 0
                    ? $"Catégorie « {trimmedName} » importée : {found} son(s) ajouté(s), {skipped} introuvable(s) sur ce serveur."
                    : $"Catégorie « {trimmedName} » importée : {found} son(s) ajouté(s).",
                skipped > 0 ? ToastKind.Warning : ToastKind.Success);
        }

        // Pas de bouton de création pour les catégories partagées : une existe automatiquement
        // pour chaque serveur Discord dont l'utilisateur est membre (voir RefreshSharedCategoriesAsync).
        // Pour la même raison, renommer/supprimer une catégorie partagée n'a pas de sens (son nom
        // suit celui du serveur Discord, et elle disparaît d'elle-même si on n'en est plus membre) :
        // ces deux actions ne concernent donc plus que les catégories personnelles.

        private void RenameCategory_Click(object sender, RoutedEventArgs e)
        {
            if (sender is not Button { Tag: SectionViewModel section } || section.IsShared) return;
            var newName = PromptDialog.Show(this, "Nouveau nom de catégorie :", section.Name);
            if (string.IsNullOrWhiteSpace(newName)) return;

            _library.RenameCategory(section.CategoryKey, newName.Trim());
            RefreshSections();
        }

        private void DeleteCategory_Click(object sender, RoutedEventArgs e)
        {
            if (sender is not Button { Tag: SectionViewModel section } || section.IsShared) return;

            var confirm = ConfirmDialog.Show(this, $"Supprimer la catégorie « {section.Name} » ?");
            if (!confirm) return;

            _library.DeleteCategory(section.CategoryKey);
            RefreshSections();
        }

        // ---------- Lecture audio ----------

        /// <summary>
        /// Joue un son dans le vocal Discord : envoie l'ordre au serveur, qui retrouve tout seul
        /// le bon salon (via votre présence vocale) et enregistre l'activité pour tous. On sonde
        /// immédiatement après l'envoi (sans attendre le prochain cycle programmé) pour que le
        /// highlight apparaisse instantanément sur votre propre clic.
        /// </summary>
        private void Play(SoundItem item) => _ = PlayAndPollAsync(item);

        private async Task PlayAndPollAsync(SoundItem item)
        {
            await _library.PlayOnServerAsync(item.Id, item.Volume);
            await PollActivityAsync();
        }

        /// <summary>Aperçu : lecture locale uniquement (icône mégaphone), jamais envoyée au serveur/bot Discord.</summary>
        private async void PreviewLocal(SoundItem item) => await PlayLocalAsync(item);

        private async Task PlayLocalAsync(SoundItem item)
        {
            var localPath = await _library.GetOrDownloadCachedFileAsync(item);
            if (localPath is null) return;

            var defaultDevice = AudioPlaybackService.GetDefaultOutputDevice();
            if (defaultDevice is null) return;

            var volume = item.Volume * _library.Settings.LocalPlaybackVolume;
            _audio.PlaySound(item.Id, localPath, new[] { new AudioPlaybackService.PlaybackTarget(defaultDevice, volume) });
        }

        /// <summary>Coupe la lecture locale ET tous les sons actuellement mixés côté serveur (dans le salon où vous êtes).</summary>
        private async void StopAllButton_Click(object sender, RoutedEventArgs e)
        {
            _audio.StopAll();
            var ok = await _library.StopAllOnServerAsync();
            if (!ok)
            {
                ToastService.Show(
                    "Impossible de couper les sons côté serveur." +
                    (string.IsNullOrEmpty(_library.LastErrorDetail) ? "" : " " + _library.LastErrorDetail),
                    ToastKind.Warning);
            }
        }

        // ---------- Paramètres ----------

        private async void SettingsButton_Click(object sender, RoutedEventArgs e)
        {
            var window = new SettingsWindow(_library.Settings, _library) { Owner = this };
            if (window.ShowDialog() == true)
            {
                _library.SaveSettings();
                ApplyColorScheme();
                ApplyTheme();
                await RefreshCatalogAsync();
                await RefreshSharedCategoriesAsync();
                await CacheMyUsernameAsync();
                await RefreshVoiceStatusAsync();
            }
        }

        // ---------- Glisser-déposer de fichiers (ajout de sons) ----------

        private void Window_DragEnter(object sender, DragEventArgs e)
        {
            if (e.Data.GetDataPresent(DataFormats.FileDrop))
            {
                e.Effects = DragDropEffects.Copy;
                DropOverlay.Visibility = Visibility.Visible;
            }
            else
            {
                e.Effects = DragDropEffects.None;
            }
        }

        private void Window_DragLeave(object sender, DragEventArgs e) => DropOverlay.Visibility = Visibility.Collapsed;

        private async void Window_Drop(object sender, DragEventArgs e)
        {
            DropOverlay.Visibility = Visibility.Collapsed;

            if (!e.Data.GetDataPresent(DataFormats.FileDrop)) return;

            if (e.Data.GetData(DataFormats.FileDrop) is string[] filePaths)
                await AddSoundFiles(filePaths);
        }

        // ---------- Palette de commande (Ctrl+K) ----------

        private void MainWindow_PreviewKeyDown(object sender, KeyEventArgs e)
        {
            if (e.Key == Key.K && Keyboard.Modifiers == ModifierKeys.Control)
            {
                e.Handled = true;
                OpenCommandPalette();
            }
        }

        private void OpenCommandPalette()
        {
            CommandPaletteOverlay.Visibility = Visibility.Visible;
            CommandPaletteBox.Text = "";
            CommandPaletteList.ItemsSource = Sounds;
            CommandPaletteBox.Focus();
        }

        private void CloseCommandPalette() => CommandPaletteOverlay.Visibility = Visibility.Collapsed;

        private void CommandPaletteOverlay_MouseLeftButtonDown(object sender, MouseButtonEventArgs e) => CloseCommandPalette();

        private void CommandPalettePanel_MouseLeftButtonDown(object sender, MouseButtonEventArgs e) => e.Handled = true;

        private void CommandPaletteBox_TextChanged(object sender, TextChangedEventArgs e)
        {
            var query = CommandPaletteBox.Text;
            CommandPaletteList.ItemsSource = string.IsNullOrWhiteSpace(query)
                ? Sounds
                : Sounds.Where(s => s.Name.Contains(query, StringComparison.OrdinalIgnoreCase)).ToList();
            if (CommandPaletteList.Items.Count > 0) CommandPaletteList.SelectedIndex = 0;
        }

        private void CommandPaletteBox_PreviewKeyDown(object sender, KeyEventArgs e)
        {
            switch (e.Key)
            {
                case Key.Escape:
                    e.Handled = true;
                    CloseCommandPalette();
                    break;
                case Key.Down:
                    e.Handled = true;
                    if (CommandPaletteList.SelectedIndex < CommandPaletteList.Items.Count - 1) CommandPaletteList.SelectedIndex++;
                    CommandPaletteList.ScrollIntoView(CommandPaletteList.SelectedItem);
                    break;
                case Key.Up:
                    e.Handled = true;
                    if (CommandPaletteList.SelectedIndex > 0) CommandPaletteList.SelectedIndex--;
                    CommandPaletteList.ScrollIntoView(CommandPaletteList.SelectedItem);
                    break;
                case Key.Enter:
                    e.Handled = true;
                    PlaySelectedFromCommandPalette();
                    break;
            }
        }

        private void CommandPaletteList_MouseDoubleClick(object sender, MouseButtonEventArgs e) => PlaySelectedFromCommandPalette();

        private void PlaySelectedFromCommandPalette()
        {
            var item = CommandPaletteList.SelectedItem as SoundItem
                ?? CommandPaletteList.Items.Cast<SoundItem>().FirstOrDefault();
            if (item is null) return;
            CloseCommandPalette();
            Play(item);
        }

        // ---------- Raccourcis clavier globaux ----------

        private void RegisterAllHotkeys()
        {
            foreach (var item in Sounds)
                RegisterHotkeyFor(item);
        }

        private void RegisterHotkeyFor(SoundItem item)
        {
            if (string.IsNullOrEmpty(item.Hotkey) || _hotkeys is null) return;

            if (HotkeyParser.TryParse(item.Hotkey, out var modifiers, out var key))
                _hotkeys.Register(modifiers, key, () => Dispatcher.Invoke(() => Play(item)));
        }

        /// <summary>Reconstruit tous les raccourcis depuis Sounds/Settings.SoundHotkeys — le
        /// GlobalHotkeyManager n'expose pas de désenregistrement ciblé par son, donc toute
        /// modification (retrait, suppression du son) repasse par un cycle complet unregister/register.</summary>
        private void ResyncHotkeys()
        {
            _hotkeys?.UnregisterAll();
            RegisterAllHotkeys();
        }

        private void AssignHotkey(SoundItem item)
        {
            var captureWindow = new HotkeyCaptureWindow { Owner = this };
            if (captureWindow.ShowDialog() == true && captureWindow.CapturedHotkey is not null)
            {
                _library.SetHotkey(item, captureWindow.CapturedHotkey);
                _hotkeys?.UnregisterAll();
                RegisterAllHotkeys();
            }
        }
    }
}
