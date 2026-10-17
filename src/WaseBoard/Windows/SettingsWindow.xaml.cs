using System;
using System.Collections.Generic;
using System.Linq;
using System.Diagnostics;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using WaseBoard.Models;
using WaseBoard.Services;
using WaseBoard.Services.Localization;
using WaseBoard.Services.MicFx;

namespace WaseBoard.Windows
{
    public partial class SettingsWindow : Window
    {
        private readonly AppSettings _settings;
        private readonly SoundLibraryService _library;
        private string? _selectedBgColorHex;
        private string _selectedPaletteId = PalettePresets.DefaultId;
        private string _selectedTheme = "Classic";
        private string? _latestReleaseUrl;

        // Les boutons "Tester"/"Vérifier"/"Rechercher une mise à jour" lancent un appel réseau
        // (async void) puis touchent l'UI une fois la réponse reçue ; si l'utilisateur ferme cette
        // fenêtre pendant l'attente (ex: réponse lente, ou serveur injoignable), la continuation
        // reprend sur une fenêtre déjà fermée — AlertDialog.Show(this, ...) plante alors
        // ("Owner sur une fenêtre fermée"). Ce drapeau permet d'abandonner proprement.
        private bool _isClosed;

        // Serveurs dont l'utilisateur est admin (vide = pas d'entrée Administration) et sons connus,
        // transmis au panel d'administration qui s'ouvre depuis cette fenêtre.
        private readonly List<SoundLibraryService.SharedCategoryInfo> _adminGuilds;
        private readonly List<SoundItem> _knownSounds;

        // Micro en jeu : service de lecture dans le micro (pour le bouton « Tester ») et cases à
        // cocher des micros, par GUID d'endpoint.
        private readonly MicFeedService? _micFeed;
        private readonly Dictionary<string, CheckBox> _micCheckBoxes = new();
        private readonly DispatcherTimer _micLiveTimer = new() { Interval = TimeSpan.FromSeconds(1) };
        private MicFxSetup.State? _micState;
        private readonly Dictionary<string, int> _micMissingStreak = new();

        /// <summary>Vrai si le panel d'administration a modifié le catalogue (renommage, suppression,
        /// restauration) : l'appelant doit alors recharger les sons, même si les Paramètres n'ont pas été enregistrés.</summary>
        public bool AdminCatalogChanged { get; private set; }

        /// <summary>Vrai si l'utilisateur s'est déconnecté de Discord depuis cette fenêtre (effet immédiat, sans « Enregistrer ») :
        /// l'appelant doit vider la page au lieu de recharger un catalogue auquel il n'a plus accès.</summary>
        public bool LoggedOut { get; private set; }

        // Raccourci de bascule Jeu/Discord en cours d'édition (écrit dans les réglages par « Enregistrer »).
        private string? _micModeHotkey;

        // Idem pour le raccourci du clip des dernières secondes.
        private string? _clipHotkey;

        public SettingsWindow(AppSettings settings, SoundLibraryService library, string initialPage = "Server",
            IEnumerable<SoundLibraryService.SharedCategoryInfo>? adminGuilds = null, IEnumerable<SoundItem>? knownSounds = null,
            MicFeedService? micFeed = null)
        {
            InitializeComponent();
            _settings = settings;
            _library = library;
            _adminGuilds = adminGuilds?.ToList() ?? new();
            _knownSounds = knownSounds?.ToList() ?? new();
            _micFeed = micFeed;
            Closed += (_, _) =>
            {
                _isClosed = true;
                _micLiveTimer.Stop();
            };
            _micLiveTimer.Tick += (_, _) => UpdateMicLiveText();

            if (_adminGuilds.Count > 0)
            {
                NavAdminButton.Visibility = Visibility.Visible;
                AdminSummaryText.Text = _adminGuilds.Count == 1
                    ? Loc.T("settings.admin.one_guild", _adminGuilds[0].GuildName)
                    : Loc.T("settings.admin.n_guilds", _adminGuilds.Count, string.Join(", ", _adminGuilds.Select(g => g.GuildName)));
            }

            ServerUrlBox.Text = _settings.ServerUrl;
            ServerTokenBox.Text = _settings.ServerToken;

            if (!string.IsNullOrEmpty(_settings.DiscordSessionToken))
            {
                ConnectDiscordButton.Content = Loc.T("settings.discord.switch_account");
                LogoutDiscordButton.Visibility = Visibility.Visible;
                ShowVerifyResult(true, Loc.T("settings.discord.connected_as", _settings.DiscordUsername ?? ""), _settings.DiscordAvatarUrl);
            }

            LocalVolumeSlider.Value = _settings.LocalPlaybackVolume;
            UpdateVolumeLabel(LocalVolumeLabel, _settings.LocalPlaybackVolume);

            MicFeatureEnabledCheckBox.IsChecked = _settings.MicFeatureEnabled;
            MicFeedVolumeSlider.Value = _settings.MicFeedVolume;
            UpdateVolumeLabel(MicFeedVolumeLabel, _settings.MicFeedVolume);
            MicFeedMonitorCheckBox.IsChecked = _settings.MicFeedMonitor;
            MicFeedAlsoDiscordCheckBox.IsChecked = _settings.MicFeedAlsoDiscord;
            _micModeHotkey = _settings.MicModeHotkey;
            UpdateMicModeHotkeyText();
            MicModeBeepCheckBox.IsChecked = _settings.MicModeBeep;

            ClipEnabledCheckBox.IsChecked = _settings.ClipEnabled;
            foreach (var seconds in LoopbackClipService.SecondsChoices)
                ClipSecondsCombo.Items.Add(new ComboBoxItem { Content = Loc.T("settings.clip.seconds_option", seconds), Tag = seconds });
            ClipSecondsCombo.SelectedIndex = Array.IndexOf(LoopbackClipService.SecondsChoices, LoopbackClipService.NormalizeSeconds(_settings.ClipSeconds));
            _clipHotkey = _settings.ClipHotkey;
            UpdateClipHotkeyText();
            ClipBeepCheckBox.IsChecked = _settings.ClipBeep;

            _selectedBgColorHex = _settings.BackgroundColorHex;
            CustomColorBox.Text = _selectedBgColorHex ?? "";
            UpdateColorPreview();

            _selectedPaletteId = _settings.PaletteId ?? PalettePresets.DefaultId;
            BuildPaletteCards();

            _selectedTheme = _settings.UiTheme;
            UpdateThemeButtons();

            FollowSystemThemeCheckBox.IsChecked = _settings.FollowSystemTheme;
            FollowSystemAccentCheckBox.IsChecked = _settings.FollowSystemAccent;
            ShowWaveformsCheckBox.IsChecked = _settings.ShowWaveforms;
            UpdateManualColorSectionEnabled();

            LanguageCombo.Items.Add(new ComboBoxItem { Content = Loc.T("settings.appearance.language_auto"), Tag = "auto" });
            LanguageCombo.Items.Add(new ComboBoxItem { Content = "Français", Tag = "fr" }); // loc-ok : le nom de la langue, dans sa langue
            LanguageCombo.Items.Add(new ComboBoxItem { Content = "English", Tag = "en" });
            var savedLanguage = (_settings.Language ?? "auto").Trim().ToLowerInvariant();
            LanguageCombo.SelectedIndex = savedLanguage switch { "fr" => 1, "en" => 2, _ => 0 };

            var currentVersion = System.Reflection.Assembly.GetExecutingAssembly().GetName().Version;
            CurrentVersionText.Text = Loc.T("settings.updates.current", currentVersion?.ToString(3) ?? "?");

            SelectPage(initialPage);
        }

        // ---------- Navigation latérale ----------

        private void NavButton_Click(object sender, RoutedEventArgs e)
        {
            if (sender is Button { Tag: string key }) SelectPage(key);
        }

        /// <summary>Affiche une seule page à la fois et met en valeur le bouton de nav correspondant
        /// — même motif que UpdateThemeButtons (Background accent/transparent géré en code-behind).</summary>
        private void SelectPage(string key)
        {
            var pages = new (string Key, StackPanel Page, Button Nav)[]
            {
                ("Server", PageServer, NavServerButton),
                ("Discord", PageDiscord, NavDiscordButton),
                ("Appearance", PageAppearance, NavAppearanceButton),
                ("Volume", PageVolume, NavVolumeButton),
                ("Mic", PageMic, NavMicButton),
                ("Clip", PageClip, NavClipButton),
                ("Updates", PageUpdates, NavUpdatesButton),
                ("Diagnostic", PageDiagnostic, NavDiagnosticButton),
                ("Admin", PageAdmin, NavAdminButton),
            };

            var accent = (Brush)FindResource("AccentBrush");
            foreach (var (pageKey, page, nav) in pages)
            {
                var isSelected = pageKey == key;
                page.Visibility = isSelected ? Visibility.Visible : Visibility.Collapsed;
                nav.Background = isSelected ? accent : Brushes.Transparent;
                nav.Foreground = (Brush)FindResource(isSelected ? "OnAccentBrush" : "TextBrush");
            }

            // La page « Diagnostic » mesure le réseau : une première fois à l'ouverture, ensuite à la demande.
            if (key == "Diagnostic" && !_diagnosticStarted)
            {
                _diagnosticStarted = true;
                _ = RunDiagnosticAsync();
            }

            // La page « Micro en jeu » lit l'état du registre et de l'effet : seulement quand elle est affichée.
            if (key == "Mic")
            {
                RefreshMicFxState();
                UpdateMicLiveText();
                _micLiveTimer.Start();
            }
            else
            {
                _micLiveTimer.Stop();
            }
        }

        // ---------- Diagnostic ----------

        private bool _diagnosticStarted;
        private string _diagnosticReport = "";

        private async void RunDiagnosticButton_Click(object sender, RoutedEventArgs e) => await RunDiagnosticAsync();

        private async System.Threading.Tasks.Task RunDiagnosticAsync()
        {
            RunDiagnosticButton.IsEnabled = false;
            CopyDiagnosticButton.IsEnabled = false;
            DiagnosticText.Text = Loc.T("settings.diagnostic.running");
            try
            {
                _diagnosticReport = await BuildDiagnosticReportAsync();
            }
            catch (Exception ex)
            {
                _diagnosticReport = ex.Message;
            }
            DiagnosticText.Text = _diagnosticReport;
            RunDiagnosticButton.IsEnabled = true;
            CopyDiagnosticButton.IsEnabled = _diagnosticReport.Length > 0;
        }

        /// <summary>Le rapport affiché ET copié : versions, latence réseau, durée des derniers clics, vocal, micro. Jamais de jeton.</summary>
        private async System.Threading.Tasks.Task<string> BuildDiagnosticReportAsync()
        {
            var report = new System.Text.StringBuilder();
            var version = System.Reflection.Assembly.GetExecutingAssembly().GetName().Version?.ToString(3) ?? "?";
#if WASEBOARD_DEV
            const string build = "Dev";
#else
            const string build = "Release";
#endif
            report.AppendLine(Loc.T("report.title"));
            report.AppendLine(Loc.T("report.client", version, build, "Windows " + Environment.OSVersion.Version,
                System.Runtime.InteropServices.RuntimeInformation.FrameworkDescription));
            report.AppendLine(Loc.T("report.language", Loc.Language, _settings.UiTheme));
            report.AppendLine();

            var serverUrl = _library.Settings.ServerUrl;
            var host = Uri.TryCreate(serverUrl, UriKind.Absolute, out var uri) ? uri.Host : serverUrl;
            report.AppendLine(Loc.T("report.server", host));

            // Un seul tour de /status : rafraîchit la version et les fonctions annoncées par le serveur.
            await _library.GetServerStatusAsync();
            report.AppendLine(_library.ServerVersion is { } serverVersion
                ? Loc.T("report.server_version", serverVersion)
                : Loc.T("report.server_version_unknown"));
            if (_library.ServerFeatures is { Count: > 0 } features)
                report.AppendLine(Loc.T("report.server_features", string.Join(", ", features.OrderBy(f => f, StringComparer.Ordinal))));

            var probe = await _library.MeasureLatencyAsync(5);
            report.AppendLine(probe.Ok
                ? Loc.T("report.ping", probe.SamplesMs.Count, probe.Median, probe.Min, probe.Max)
                : Loc.T("report.ping_failed", probe.Error ?? "?"));

            if (_library.Latency.Summarize() is { } plays)
            {
                var line = Loc.T("report.plays", plays.Count, plays.MedianTotalMs, plays.MaxTotalMs);
                if (plays.MedianServerMs is { } serverMs) line += Loc.T("report.plays_server", serverMs);
                report.AppendLine(line);
            }
            else
            {
                report.AppendLine(Loc.T("report.plays_none"));
            }
            report.AppendLine();

            var voice = await _library.GetLiveVoiceStatusAsync();
            report.AppendLine(voice.Connected
                ? Loc.T("report.voice_connected", voice.Channel ?? "?", voice.GuildName ?? "?")
                : Loc.T("report.voice_disconnected"));
            report.AppendLine(Loc.T("report.mic", _settings.MicFeatureEnabled
                ? Loc.T("report.mic_on", Loc.T(_settings.MicFeedEnabled ? "report.mode_game" : "report.mode_discord"))
                : Loc.T("report.mic_off")));
            report.AppendLine(Loc.T("report.session", Loc.T(string.IsNullOrEmpty(_settings.DiscordSessionToken) ? "report.session_off" : "report.session_on")));
            return report.ToString().TrimEnd();
        }

        private void CopyDiagnosticButton_Click(object sender, RoutedEventArgs e)
        {
            if (_diagnosticReport.Length == 0) return;
            Clipboard.SetText(_diagnosticReport);

            var original = CopyDiagnosticButton.Content;
            CopyDiagnosticButton.Content = Loc.T("settings.diagnostic.copied");
            var timer = new System.Windows.Threading.DispatcherTimer { Interval = TimeSpan.FromSeconds(2) };
            timer.Tick += (_, _) => { CopyDiagnosticButton.Content = original; timer.Stop(); };
            timer.Start();
        }

        private void OpenAdminPanelButton_Click(object sender, RoutedEventArgs e)
        {
            if (_adminGuilds.Count == 0) return;

            var panel = new AdminPanelWindow(_library, _adminGuilds, _knownSounds) { Owner = this };
            panel.ShowDialog();
            if (panel.CatalogChanged) AdminCatalogChanged = true;
        }

        private void FollowSystemTheme_Changed(object sender, RoutedEventArgs e) => UpdateManualColorSectionEnabled();

        /// <summary>La couleur de fond personnalisée n'a d'effet que si le thème système n'est pas suivi.</summary>
        private void UpdateManualColorSectionEnabled()
        {
            if (ManualColorSection is null) return; // encore en cours d'InitializeComponent
            var enabled = FollowSystemThemeCheckBox.IsChecked != true;
            ManualColorSection.IsEnabled = enabled;
            ManualColorSection.Opacity = enabled ? 1.0 : 0.4;
        }

        private void ClassicThemeButton_Click(object sender, RoutedEventArgs e)
        {
            _selectedTheme = "Classic";
            UpdateThemeButtons();
        }

        private void ModernThemeButton_Click(object sender, RoutedEventArgs e)
        {
            _selectedTheme = "Modern";
            UpdateThemeButtons();
        }

        private void FlatThemeButton_Click(object sender, RoutedEventArgs e)
        {
            _selectedTheme = "Flat";
            UpdateThemeButtons();
        }

        private void UpdateThemeButtons()
        {
            var accent = (System.Windows.Media.Brush)FindResource("AccentBrush");
            var transparent = System.Windows.Media.Brushes.Transparent;

            ClassicThemeButton.Background = _selectedTheme == "Classic" ? accent : transparent;
            ModernThemeButton.Background = _selectedTheme == "Modern" ? accent : transparent;
            FlatThemeButton.Background = _selectedTheme == "Flat" ? accent : transparent;
        }

        /// <summary>Une carte par palette (fond, panneau, accent et nom dans ses propres couleurs), construite
        /// en code : la liste vient de PalettePresets, pas d'un XAML à tenir à jour.</summary>
        private void BuildPaletteCards()
        {
            PaletteList.Children.Clear();
            foreach (var preset in PalettePresets.All)
            {
                var card = new Border
                {
                    Width = 104, Height = 62, Margin = new Thickness(0, 0, 8, 8), CornerRadius = new CornerRadius(9),
                    Background = new SolidColorBrush(preset.Bg), BorderThickness = new Thickness(2),
                    Cursor = Cursors.Hand, Tag = preset.Id, ToolTip = preset.Name
                };
                var bar = new Border
                {
                    Height = 20, CornerRadius = new CornerRadius(10), Background = new SolidColorBrush(preset.Panel),
                    VerticalAlignment = VerticalAlignment.Top, Margin = new Thickness(8, 8, 8, 0),
                    Child = new System.Windows.Shapes.Ellipse
                    {
                        Width = 10, Height = 10, Fill = new SolidColorBrush(preset.Accent),
                        HorizontalAlignment = HorizontalAlignment.Left, Margin = new Thickness(6, 0, 0, 0)
                    }
                };
                var name = new TextBlock
                {
                    Text = preset.Name, FontSize = 11, FontWeight = FontWeights.SemiBold,
                    Foreground = new SolidColorBrush(preset.Text), VerticalAlignment = VerticalAlignment.Bottom,
                    Margin = new Thickness(10, 0, 0, 7)
                };
                var content = new Grid();
                content.Children.Add(bar);
                content.Children.Add(name);
                card.Child = content;
                card.MouseLeftButtonDown += PaletteCard_Click;
                PaletteList.Children.Add(card);
            }
            UpdatePaletteSelection();
        }

        private void PaletteCard_Click(object sender, MouseButtonEventArgs e)
        {
            if (sender is not FrameworkElement { Tag: string id }) return;
            _selectedPaletteId = id;
            // Choisir une palette remplace un éventuel fond personnalisé : sinon le choix semblerait sans effet.
            _selectedBgColorHex = null;
            CustomColorBox.Text = "";
            UpdateColorPreview();
            UpdatePaletteSelection();
        }

        private void UpdatePaletteSelection()
        {
            foreach (var child in PaletteList.Children.OfType<Border>())
            {
                var preset = PalettePresets.Get(child.Tag as string);
                child.BorderBrush = child.Tag as string == _selectedPaletteId
                    ? new SolidColorBrush(preset.Accent)
                    : (Brush)FindResource("TrackBrush");
            }
        }

        private void ClearCustomColor_Click(object sender, RoutedEventArgs e)
        {
            _selectedBgColorHex = null;
            CustomColorBox.Text = "";
            UpdateColorPreview();
        }

        private void ApplyCustomColor_Click(object sender, RoutedEventArgs e)
        {
            var text = CustomColorBox.Text.Trim();
            if (text.Length == 0) { ClearCustomColor_Click(sender, e); return; }
            if (!text.StartsWith("#")) text = "#" + text;

            try
            {
                _ = (Color)ColorConverter.ConvertFromString(text); // valide le format avant d'accepter
                _selectedBgColorHex = text;
                UpdateColorPreview();
            }
            catch
            {
                AlertDialog.Show(this, Loc.T("settings.appearance.invalid_color"),
                    "WaseBoard", AlertKind.Warning);
            }
        }

        private void UpdateColorPreview()
        {
            if (string.IsNullOrEmpty(_selectedBgColorHex))
            {
                ColorPreview.Background = Brushes.Transparent;
                return;
            }
            try
            {
                ColorPreview.Background = new SolidColorBrush((Color)ColorConverter.ConvertFromString(_selectedBgColorHex));
            }
            catch { /* couleur invalide : on garde le dernier aperçu valide */ }
        }

        private static void UpdateVolumeLabel(TextBlock label, double value) =>
            label.Text = $"{(int)(value * 100)}%";

        private void LocalVolumeSlider_ValueChanged(object sender, RoutedPropertyChangedEventArgs<double> e)
        {
            if (LocalVolumeLabel is not null) UpdateVolumeLabel(LocalVolumeLabel, e.NewValue);
        }

        // ---------- Micro en jeu ----------

        private void MicFeedVolumeSlider_ValueChanged(object sender, RoutedPropertyChangedEventArgs<double> e)
        {
            if (MicFeedVolumeLabel is not null) UpdateVolumeLabel(MicFeedVolumeLabel, e.NewValue);
        }

        /// <summary>Relit l'installation (registre) et reconstruit la liste des micros. Cochés par
        /// défaut : les micros déjà équipés (ou à réparer), sinon le micro de communication par défaut.</summary>
        private void RefreshMicFxState()
        {
            var state = MicFxSetup.ReadState();
            _micState = state;

            MicFxMicList.Children.Clear();
            _micCheckBoxes.Clear();
            foreach (var mic in state.Microphones)
            {
                var label = mic.Name;
                if (mic.IsDefaultCommunications) label += Loc.T("settings.mic.label_default");
                if (!mic.Compatible) label += Loc.T("settings.mic.label_incompatible");
                else if (mic.NeedsRepair) label += Loc.T("settings.mic.label_repair");
                else if (mic.Equipped) label += Loc.T("settings.mic.label_installed");

                var checkBox = new CheckBox
                {
                    Content = label,
                    IsEnabled = mic.Compatible,
                    IsChecked = mic.Compatible && (mic.Equipped || mic.NeedsRepair
                        || (!state.AnyEquipped && mic.IsDefaultCommunications)),
                    Margin = new Thickness(0, 0, 0, 4),
                };
                checkBox.SetResourceReference(ForegroundProperty, "TextBrush");
                MicFxMicList.Children.Add(checkBox);
                _micCheckBoxes[mic.EndpointGuid] = checkBox;
            }

            if (!MicFxSetup.IsBundled)
            {
                SetMicStatus(MicFxStatusText, Loc.T("settings.mic.status.absent"), WarningBrush);
                MicFxInstallButton.IsEnabled = false;
            }
            else if (state.Microphones.Count == 0)
            {
                SetMicStatus(MicFxStatusText, Loc.T("settings.mic.status.none"), WarningBrush);
                MicFxInstallButton.IsEnabled = false;
            }
            else if (state.AnyNeedsRepair)
            {
                SetMicStatus(MicFxStatusText, Loc.T("settings.mic.status.repair"), WarningBrush);
                MicFxInstallButton.Content = Loc.T("settings.mic.repair");
                MicFxInstallButton.IsEnabled = true;
            }
            else if (state.NeedsUpdate)
            {
                SetMicStatus(MicFxStatusText, Loc.T("settings.mic.status.update"), WarningBrush);
                MicFxInstallButton.Content = Loc.T("settings.mic.update");
                MicFxInstallButton.IsEnabled = true;
            }
            else if (state.Registered && state.AnyEquipped)
            {
                SetMicStatus(MicFxStatusText, Loc.T("settings.mic.status.installed"), SuccessBrush);
                MicFxInstallButton.Content = Loc.T("common.apply");
                MicFxInstallButton.IsEnabled = true;
            }
            else
            {
                SetMicStatus(MicFxStatusText, Loc.T("settings.mic.status.pick"), null);
                MicFxInstallButton.Content = Loc.T("settings.mic.install");
                MicFxInstallButton.IsEnabled = true;
            }

            MicFxUninstallButton.IsEnabled = state.Registered || state.AnyEquipped;
            MicFxTestButton.IsEnabled = _micFeed is not null && state.AnyEquipped;
        }

        /// <summary>État en direct, micro par micro (voir MicFxDiagnostics). « Écouté mais effet inactif »
        /// n'est signalé qu'au 2e constat d'affilée : à l'ouverture d'un flux, l'effet met quelques
        /// millisecondes à démarrer.</summary>
        private void UpdateMicLiveText()
        {
            var state = _micState;
            if (state is null || !state.AnyEquipped || state.NeedsUpdate)
            {
                SetMicStatus(MicFxLiveText, "", null); // la ligne d'état au-dessus dit déjà quoi faire
                return;
            }

            var activity = MicFxDiagnostics.Probe(state.Microphones);
            foreach (var mic in activity)
                _micMissingStreak[mic.EndpointGuid] = mic.EffectMissing ? _micMissingStreak.GetValueOrDefault(mic.EndpointGuid) + 1 : 0;

            var missing = activity.FirstOrDefault(m => m.EffectMissing && _micMissingStreak[m.EndpointGuid] >= 2);
            var active = activity.FirstOrDefault(m => m.EffectActive);
            if (missing is not null)
                SetMicStatus(MicFxLiveText, Loc.T("settings.mic.live.missing", missing.Name), WarningBrush);
            else if (active is not null)
                SetMicStatus(MicFxLiveText, Loc.T("settings.mic.live.active", active.Name), SuccessBrush);
            else
                SetMicStatus(MicFxLiveText, Loc.T("settings.mic.live.ready"), null);
        }

        // Pas d'emoji d'état ici : WPF les affiche en noir et blanc, et ✅ y ressemble à une case à cocher.
        private static readonly Brush SuccessBrush = new SolidColorBrush(Color.FromRgb(0x4C, 0xAF, 0x50));
        private static readonly Brush WarningBrush = new SolidColorBrush(Color.FromRgb(0xF5, 0xA6, 0x23));

        /// <summary>Texte d'état en couleur (vert : OK, orange : à corriger), ou couleur de texte normale si null.</summary>
        private static void SetMicStatus(TextBlock target, string text, Brush? color)
        {
            target.Text = text;
            if (color is null) target.SetResourceReference(TextBlock.ForegroundProperty, "TextBrush");
            else target.Foreground = color;
        }

        private async void MicFxInstallButton_Click(object sender, RoutedEventArgs e)
        {
            var selected = _micCheckBoxes.Where(kv => kv.Value.IsChecked == true).Select(kv => kv.Key).ToList();
            if (selected.Count == 0)
            {
                AlertDialog.Show(this, Loc.T("settings.mic.pick_one"), Loc.T("settings.mic.title"), AlertKind.Warning);
                return;
            }
            if (!ConfirmDialog.Show(this,
                    Loc.T("settings.mic.confirm_install"),
                    Loc.T("settings.mic.title")))
                return;

            MicFxInstallButton.IsEnabled = false;
            var result = await MicFxSetup.InstallAsync(selected);
            if (_isClosed) return;

            RefreshMicFxState();
            if (result.Cancelled) return;
            if (result.Ok) MicFeatureEnabledCheckBox.IsChecked = true;
            AlertDialog.Show(this, string.Join("\n", result.Messages), Loc.T("settings.mic.title"),
                result.Ok ? AlertKind.Info : AlertKind.Warning);
        }

        private async void MicFxUninstallButton_Click(object sender, RoutedEventArgs e)
        {
            if (!ConfirmDialog.Show(this,
                    Loc.T("settings.mic.confirm_uninstall"),
                    Loc.T("settings.mic.title")))
                return;

            MicFxUninstallButton.IsEnabled = false;
            var result = await MicFxSetup.UninstallAsync();
            if (_isClosed) return;

            RefreshMicFxState();
            if (result.Cancelled) return;
            if (result.Ok) MicFeatureEnabledCheckBox.IsChecked = false;
            AlertDialog.Show(this, string.Join("\n", result.Messages), Loc.T("settings.mic.title"),
                result.Ok ? AlertKind.Info : AlertKind.Warning);
        }

        private void MicFxTestButton_Click(object sender, RoutedEventArgs e)
        {
            if (_micFeed is null) return;
            _micFeed.Volume = (float)MicFeedVolumeSlider.Value;
            _micFeed.PlayTestTone();
            if (!MicFeedService.ReadStatus().MicInUse)
                AlertDialog.Show(this,
                    Loc.T("settings.mic.beep_sent"),
                    Loc.T("settings.mic.title"));
        }

        private async void TestConnectionButton_Click(object sender, RoutedEventArgs e)
        {
            var previousUrl = _settings.ServerUrl;
            var previousToken = _settings.ServerToken;
            _settings.ServerUrl = string.IsNullOrWhiteSpace(ServerUrlBox.Text) ? previousUrl : ServerUrlBox.Text.Trim();
            _settings.ServerToken = ServerTokenBox.Text;

            TestConnectionButton.IsEnabled = false;
            var (result, channel) = await _library.GetServerStatusAsync();
            if (_isClosed) return;
            TestConnectionButton.IsEnabled = true;

            var message = result switch
            {
                SoundLibraryService.ServerStatusResult.Connected =>
                    Loc.T("settings.server.test.connected", channel ?? ""),
                SoundLibraryService.ServerStatusResult.BotNotInVoice =>
                    Loc.T("settings.server.test.no_voice"),
                SoundLibraryService.ServerStatusResult.Unauthorized =>
                    Loc.T("settings.server.test.unauthorized"),
                _ => Loc.T("settings.server.test.unreachable") +
                     (string.IsNullOrEmpty(_library.LastErrorDetail) ? "" : "\n" + Loc.T("common.detail", _library.LastErrorDetail))
            };

            var isOk = result is SoundLibraryService.ServerStatusResult.Connected or SoundLibraryService.ServerStatusResult.BotNotInVoice;
            AlertDialog.Show(this, message, Loc.T("server.title"), isOk ? AlertKind.Info : AlertKind.Warning);

            _settings.ServerUrl = previousUrl;
            _settings.ServerToken = previousToken;
        }

        /// <summary>Construit un lien waseboard://connect à partir des champs actuellement
        /// affichés (pas besoin d'avoir cliqué "Enregistrer") et le copie dans le presse-papier,
        /// pour inviter quelqu'un sans qu'il ait à saisir l'adresse/le jeton lui-même.</summary>
        private void CopyInviteLinkButton_Click(object sender, RoutedEventArgs e)
        {
            var url = ServerUrlBox.Text.Trim();
            var token = ServerTokenBox.Text;
            if (string.IsNullOrEmpty(url) || string.IsNullOrEmpty(token))
            {
                AlertDialog.Show(this, Loc.T("settings.server.fill_first"), "WaseBoard", AlertKind.Warning);
                return;
            }

            var link = $"waseboard://connect?url={Uri.EscapeDataString(url)}&token={Uri.EscapeDataString(token)}";
            Clipboard.SetText(link);

            var original = CopyInviteLinkButton.Content;
            CopyInviteLinkButton.Content = Loc.T("settings.diagnostic.copied");
            var timer = new System.Windows.Threading.DispatcherTimer { Interval = System.TimeSpan.FromSeconds(2) };
            timer.Tick += (_, _) => { CopyInviteLinkButton.Content = original; timer.Stop(); };
            timer.Start();
        }

        private async void ConnectDiscordButton_Click(object sender, RoutedEventArgs e)
        {
            // Applique temporairement l'URL/le jeton actuellement saisis (comme "Tester la
            // connexion") : sinon, tant que "Enregistrer" n'a pas été cliqué, la connexion
            // utilise l'ancien jeton enregistré (potentiellement vide) et échoue en 401 même
            // avec un jeton correct fraîchement tapé.
            var previousUrl = _settings.ServerUrl;
            var previousToken = _settings.ServerToken;
            _settings.ServerUrl = string.IsNullOrWhiteSpace(ServerUrlBox.Text) ? previousUrl : ServerUrlBox.Text.Trim();
            _settings.ServerToken = ServerTokenBox.Text;

            ConnectDiscordButton.IsEnabled = false;
            ShowVerifyResult(true, Loc.T("settings.discord.in_progress"), null);

            var oauth = new DiscordOAuthService(_library);
            var result = await oauth.LoginAsync();
            if (_isClosed) return;

            ConnectDiscordButton.IsEnabled = true;
            _settings.ServerUrl = previousUrl;
            _settings.ServerToken = previousToken;

            if (result.Success)
            {
                _settings.DiscordSessionToken = result.SessionToken;
                _settings.DiscordUserId = result.UserId;
                _settings.DiscordUsername = result.Username;
                _settings.DiscordAvatarUrl = result.AvatarUrl;
                _library.SaveSettings();

                ConnectDiscordButton.Content = Loc.T("settings.discord.switch_account");
                LogoutDiscordButton.Visibility = Visibility.Visible;
                LoggedOut = false;
                ShowVerifyResult(true, Loc.T("settings.discord.connected_as", result.Username ?? ""), result.AvatarUrl);
            }
            else
            {
                ShowVerifyResult(false, "❌ " + (result.Error ?? Loc.T("settings.discord.failed")), null);
            }
        }

        private async void LogoutDiscordButton_Click(object sender, RoutedEventArgs e)
        {
            if (!ConfirmDialog.Show(this, Loc.T("settings.discord.logout_confirm"), Loc.T("settings.discord.logout_title"))) return;

            LogoutDiscordButton.IsEnabled = false;
            ConnectDiscordButton.IsEnabled = false;
            ShowVerifyResult(true, Loc.T("settings.discord.logging_out"), null);

            var outcome = await _library.LogoutAsync();
            if (_isClosed) { LoggedOut = true; return; }

            LoggedOut = true;
            LogoutDiscordButton.IsEnabled = true;
            ConnectDiscordButton.IsEnabled = true;
            LogoutDiscordButton.Visibility = Visibility.Collapsed;
            ConnectDiscordButton.Content = Loc.T("settings.discord.connect");
            ShowVerifyResult(outcome == SoundLibraryService.LogoutOutcome.Done,
                Loc.T(outcome == SoundLibraryService.LogoutOutcome.Done ? "settings.discord.logged_out" : "settings.discord.logged_out_unconfirmed"), null);
        }

        // ---------- Raccourci de bascule Jeu / Discord ----------

        private void UpdateMicModeHotkeyText()
        {
            MicModeHotkeyText.Text = string.IsNullOrEmpty(_micModeHotkey) ? Loc.T("settings.mic.hotkey_none") : _micModeHotkey;
            MicModeHotkeyText.Opacity = string.IsNullOrEmpty(_micModeHotkey) ? 0.6 : 1.0;
            MicModeHotkeyClearButton.IsEnabled = !string.IsNullOrEmpty(_micModeHotkey);
        }

        private void MicModeHotkeySetButton_Click(object sender, RoutedEventArgs e)
        {
            var capture = new HotkeyCaptureWindow { Owner = this };
            if (capture.ShowDialog() != true || capture.CapturedHotkey is not { } hotkey) return;

            // Un raccourci ne peut servir qu'à une chose : refus s'il appartient déjà à un son.
            var taken = _knownSounds.FirstOrDefault(s => string.Equals(s.Hotkey, hotkey, StringComparison.OrdinalIgnoreCase));
            if (taken is not null)
            {
                AlertDialog.Show(this, Loc.T("settings.mic.hotkey_conflict", taken.Name), "WaseBoard", AlertKind.Warning);
                return;
            }
            if (string.Equals(_clipHotkey, hotkey, StringComparison.OrdinalIgnoreCase))
            {
                AlertDialog.Show(this, Loc.T("settings.mic.hotkey_conflict_clip"), "WaseBoard", AlertKind.Warning);
                return;
            }

            _micModeHotkey = hotkey;
            UpdateMicModeHotkeyText();
        }

        private void MicModeHotkeyClearButton_Click(object sender, RoutedEventArgs e)
        {
            _micModeHotkey = null;
            UpdateMicModeHotkeyText();
        }

        // ---------- Raccourci du clip des dernières secondes ----------

        private void UpdateClipHotkeyText()
        {
            ClipHotkeyText.Text = string.IsNullOrEmpty(_clipHotkey) ? Loc.T("settings.mic.hotkey_none") : _clipHotkey;
            ClipHotkeyText.Opacity = string.IsNullOrEmpty(_clipHotkey) ? 0.6 : 1.0;
            ClipHotkeyClearButton.IsEnabled = !string.IsNullOrEmpty(_clipHotkey);
        }

        private void ClipHotkeySetButton_Click(object sender, RoutedEventArgs e)
        {
            var capture = new HotkeyCaptureWindow { Owner = this };
            if (capture.ShowDialog() != true || capture.CapturedHotkey is not { } hotkey) return;

            var taken = _knownSounds.FirstOrDefault(s => string.Equals(s.Hotkey, hotkey, StringComparison.OrdinalIgnoreCase));
            if (taken is not null)
            {
                AlertDialog.Show(this, Loc.T("settings.mic.hotkey_conflict", taken.Name), "WaseBoard", AlertKind.Warning);
                return;
            }
            if (string.Equals(_micModeHotkey, hotkey, StringComparison.OrdinalIgnoreCase))
            {
                AlertDialog.Show(this, Loc.T("settings.clip.hotkey_conflict_mic"), "WaseBoard", AlertKind.Warning);
                return;
            }

            _clipHotkey = hotkey;
            UpdateClipHotkeyText();
        }

        private void ClipHotkeyClearButton_Click(object sender, RoutedEventArgs e)
        {
            _clipHotkey = null;
            UpdateClipHotkeyText();
        }

        private void ShowVerifyResult(bool success, string message, string? avatarUrl)
        {
            VerifyResultBorder.Visibility = Visibility.Visible;
            VerifyResultBorder.Background = new SolidColorBrush(success ? Color.FromRgb(0x1B, 0x3A, 0x1B) : Color.FromRgb(0x3A, 0x1B, 0x1B));
            VerifyResultText.Text = message;
            VerifyResultText.Foreground = new SolidColorBrush(Colors.White);

            if (!string.IsNullOrEmpty(avatarUrl))
            {
                try
                {
                    var image = new BitmapImage();
                    image.BeginInit();
                    image.UriSource = new System.Uri(avatarUrl, System.UriKind.Absolute);
                    image.CacheOption = BitmapCacheOption.OnLoad;
                    image.EndInit();
                    VerifyAvatarBrush.ImageSource = image;
                    VerifyAvatarBorder.Visibility = Visibility.Visible;
                }
                catch { VerifyAvatarBorder.Visibility = Visibility.Collapsed; }
            }
            else
            {
                VerifyAvatarBorder.Visibility = Visibility.Collapsed;
            }
        }

        private async void CheckUpdateButton_Click(object sender, RoutedEventArgs e)
        {
            var currentVersion = System.Reflection.Assembly.GetExecutingAssembly().GetName().Version ?? new System.Version(0, 0, 0);

            CheckUpdateButton.IsEnabled = false;
            OpenReleaseButton.Visibility = Visibility.Collapsed;
            UpdateResultText.Text = Loc.T("settings.updates.checking");

            var result = await UpdateCheckService.CheckForUpdateAsync(currentVersion);
            if (_isClosed) return;

            CheckUpdateButton.IsEnabled = true;

            if (!string.IsNullOrEmpty(result.Error))
            {
                UpdateResultText.Text = Loc.T("settings.updates.failed", result.Error);
            }
            else if (result.Available)
            {
                UpdateResultText.Text = Loc.T("settings.updates.available", result.LatestVersion ?? "");
                _latestReleaseUrl = result.ReleaseUrl;
                OpenReleaseButton.Visibility = string.IsNullOrEmpty(_latestReleaseUrl) ? Visibility.Collapsed : Visibility.Visible;
            }
            else
            {
                UpdateResultText.Text = Loc.T("settings.updates.up_to_date");
            }
        }

        private void OpenReleaseButton_Click(object sender, RoutedEventArgs e)
        {
            if (string.IsNullOrEmpty(_latestReleaseUrl)) return;
            Process.Start(new ProcessStartInfo(_latestReleaseUrl) { UseShellExecute = true });
        }

        private void SaveButton_Click(object sender, RoutedEventArgs e)
        {
            _settings.ServerUrl = string.IsNullOrWhiteSpace(ServerUrlBox.Text)
                ? _settings.ServerUrl : ServerUrlBox.Text.Trim();
            _settings.ServerToken = ServerTokenBox.Text;
            _settings.LocalPlaybackVolume = (float)LocalVolumeSlider.Value;
            // Fonction tout juste activée : on démarre en mode jeu (c'est ce qu'on vient chercher) ;
            // ensuite, le bouton 🎮/🎧 de la page principale bascule librement.
            var micFeatureEnabled = MicFeatureEnabledCheckBox.IsChecked == true;
            if (micFeatureEnabled && !_settings.MicFeatureEnabled) _settings.MicFeedEnabled = true;
            _settings.MicFeatureEnabled = micFeatureEnabled;
            _settings.MicFeedVolume = (float)MicFeedVolumeSlider.Value;
            _settings.MicFeedMonitor = MicFeedMonitorCheckBox.IsChecked == true;
            _settings.MicFeedAlsoDiscord = MicFeedAlsoDiscordCheckBox.IsChecked == true;
            _settings.BackgroundColorHex = _selectedBgColorHex;
            _settings.PaletteId = _selectedPaletteId;
            _settings.UiTheme = _selectedTheme;
            _settings.FollowSystemTheme = FollowSystemThemeCheckBox.IsChecked == true;
            _settings.FollowSystemAccent = FollowSystemAccentCheckBox.IsChecked == true;
            _settings.ShowWaveforms = ShowWaveformsCheckBox.IsChecked == true;
            _settings.MicModeHotkey = _micModeHotkey;
            _settings.MicModeBeep = MicModeBeepCheckBox.IsChecked == true;
            _settings.ClipEnabled = ClipEnabledCheckBox.IsChecked == true;
            _settings.ClipSeconds = ClipSecondsCombo.SelectedItem is ComboBoxItem { Tag: int seconds } ? seconds : LoopbackClipService.DefaultSeconds;
            _settings.ClipHotkey = _clipHotkey;
            _settings.ClipBeep = ClipBeepCheckBox.IsChecked == true;

            var newLanguage = LanguageCombo.SelectedItem is ComboBoxItem { Tag: string code } ? code : "auto";
            var languageChanged = !string.Equals((_settings.Language ?? "auto"), newLanguage, StringComparison.OrdinalIgnoreCase);
            _settings.Language = newLanguage;
            if (languageChanged)
                AlertDialog.Show(this, Loc.T("settings.appearance.language_restart"), "WaseBoard", AlertKind.Info);

            DialogResult = true;
            Close();
        }
    }
}
