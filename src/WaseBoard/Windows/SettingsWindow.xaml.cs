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
                    ? $"Vous administrez {_adminGuilds[0].GuildName}."
                    : $"Vous administrez {_adminGuilds.Count} serveurs : " + string.Join(", ", _adminGuilds.Select(g => g.GuildName)) + ".";
            }

            ServerUrlBox.Text = _settings.ServerUrl;
            ServerTokenBox.Text = _settings.ServerToken;

            if (!string.IsNullOrEmpty(_settings.DiscordSessionToken))
            {
                ConnectDiscordButton.Content = "Changer de compte";
                ShowVerifyResult(true, $"✅ Connecté en tant que {_settings.DiscordUsername}", _settings.DiscordAvatarUrl);
            }

            LocalVolumeSlider.Value = _settings.LocalPlaybackVolume;
            UpdateVolumeLabel(LocalVolumeLabel, _settings.LocalPlaybackVolume);

            MicFeatureEnabledCheckBox.IsChecked = _settings.MicFeatureEnabled;
            MicFeedVolumeSlider.Value = _settings.MicFeedVolume;
            UpdateVolumeLabel(MicFeedVolumeLabel, _settings.MicFeedVolume);
            MicFeedMonitorCheckBox.IsChecked = _settings.MicFeedMonitor;
            MicFeedAlsoDiscordCheckBox.IsChecked = _settings.MicFeedAlsoDiscord;

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

            var currentVersion = System.Reflection.Assembly.GetExecutingAssembly().GetName().Version;
            CurrentVersionText.Text = $"Version actuelle : {currentVersion?.ToString(3) ?? "?"}";

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
                ("Updates", PageUpdates, NavUpdatesButton),
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
                AlertDialog.Show(this, "Couleur invalide. Utilisez un code hexadécimal, ex: #101820",
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
                if (mic.IsDefaultCommunications) label += " (par défaut)";
                if (!mic.Compatible) label += " — non compatible";
                else if (mic.NeedsRepair) label += " — à réparer";
                else if (mic.Equipped) label += " — installé";

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
                SetMicStatus(MicFxStatusText, "Module micro absent de cette version de WaseBoard.", WarningBrush);
                MicFxInstallButton.IsEnabled = false;
            }
            else if (state.Microphones.Count == 0)
            {
                SetMicStatus(MicFxStatusText, "Aucun micro détecté.", WarningBrush);
                MicFxInstallButton.IsEnabled = false;
            }
            else if (state.AnyNeedsRepair)
            {
                SetMicStatus(MicFxStatusText, "À réparer (souvent après une mise à jour du pilote du micro).", WarningBrush);
                MicFxInstallButton.Content = "Réparer";
                MicFxInstallButton.IsEnabled = true;
            }
            else if (state.NeedsUpdate)
            {
                SetMicStatus(MicFxStatusText, "Mise à jour de l'effet disponible.", WarningBrush);
                MicFxInstallButton.Content = "Mettre à jour";
                MicFxInstallButton.IsEnabled = true;
            }
            else if (state.Registered && state.AnyEquipped)
            {
                SetMicStatus(MicFxStatusText, "Installé.", SuccessBrush);
                MicFxInstallButton.Content = "Appliquer";
                MicFxInstallButton.IsEnabled = true;
            }
            else
            {
                SetMicStatus(MicFxStatusText, "Cochez le micro que vous utilisez en jeu, puis « Installer ».", null);
                MicFxInstallButton.Content = "Installer";
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
                SetMicStatus(MicFxLiveText, $"« {missing.Name} » : une application écoute ce micro mais l'effet ne s'active pas. Ce micro n'est peut-être pas compatible.", WarningBrush);
            else if (active is not null)
                SetMicStatus(MicFxLiveText, $"Actif sur « {active.Name} ».", SuccessBrush);
            else
                SetMicStatus(MicFxLiveText, "Prêt : s'active dès qu'une application écoute votre micro.", null);
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
                AlertDialog.Show(this, "Cochez au moins un micro.", "Micro en jeu", AlertKind.Warning);
                return;
            }
            if (!ConfirmDialog.Show(this,
                    "Windows va demander les droits administrateur, et le son se coupera 2 secondes.\n\nContinuer ?",
                    "Micro en jeu"))
                return;

            MicFxInstallButton.IsEnabled = false;
            var result = await MicFxSetup.InstallAsync(selected);
            if (_isClosed) return;

            RefreshMicFxState();
            if (result.Cancelled) return;
            if (result.Ok) MicFeatureEnabledCheckBox.IsChecked = true;
            AlertDialog.Show(this, string.Join("\n", result.Messages), "Micro en jeu",
                result.Ok ? AlertKind.Info : AlertKind.Warning);
        }

        private async void MicFxUninstallButton_Click(object sender, RoutedEventArgs e)
        {
            if (!ConfirmDialog.Show(this,
                    "Retirer le micro en jeu de tous les micros ? Le son se coupera 2 secondes.",
                    "Micro en jeu"))
                return;

            MicFxUninstallButton.IsEnabled = false;
            var result = await MicFxSetup.UninstallAsync();
            if (_isClosed) return;

            RefreshMicFxState();
            if (result.Cancelled) return;
            if (result.Ok) MicFeatureEnabledCheckBox.IsChecked = false;
            AlertDialog.Show(this, string.Join("\n", result.Messages), "Micro en jeu",
                result.Ok ? AlertKind.Info : AlertKind.Warning);
        }

        private void MicFxTestButton_Click(object sender, RoutedEventArgs e)
        {
            if (_micFeed is null) return;
            _micFeed.Volume = (float)MicFeedVolumeSlider.Value;
            _micFeed.PlayTestTone();
            if (!MicFeedService.ReadStatus().MicInUse)
                AlertDialog.Show(this,
                    "Bip envoyé. Pour l'entendre, ouvrez une application qui écoute votre micro (l'Enregistreur vocal de Windows par exemple), puis recliquez « Tester ».",
                    "Micro en jeu");
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
                    $"✅ Serveur joignable. Bot connecté au salon vocal « {channel} ».",
                SoundLibraryService.ServerStatusResult.BotNotInVoice =>
                    "✅ Serveur joignable, jeton correct. Le bot n'est simplement dans aucun salon vocal pour l'instant — " +
                    "faites « /join » dans Discord, ou utilisez le bouton « 🔊 Rejoindre mon vocal » dans WaseBoard.",
                SoundLibraryService.ServerStatusResult.Unauthorized =>
                    "❌ Jeton d'accès incorrect.",
                _ => "❌ Serveur injoignable." +
                     (string.IsNullOrEmpty(_library.LastErrorDetail) ? "" : "\nDétail : " + _library.LastErrorDetail)
            };

            var isOk = result is SoundLibraryService.ServerStatusResult.Connected or SoundLibraryService.ServerStatusResult.BotNotInVoice;
            AlertDialog.Show(this, message, "Serveur WaseBoard", isOk ? AlertKind.Info : AlertKind.Warning);

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
                AlertDialog.Show(this, "Renseignez d'abord l'adresse du serveur et le jeton d'accès.", "WaseBoard", AlertKind.Warning);
                return;
            }

            var link = $"waseboard://connect?url={Uri.EscapeDataString(url)}&token={Uri.EscapeDataString(token)}";
            Clipboard.SetText(link);

            var original = CopyInviteLinkButton.Content;
            CopyInviteLinkButton.Content = "✅ Copié !";
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
            ShowVerifyResult(true, "Connexion en cours — suivez les instructions dans votre navigateur...", null);

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

                ConnectDiscordButton.Content = "Changer de compte";
                ShowVerifyResult(true, $"✅ Connecté en tant que {result.Username}", result.AvatarUrl);
            }
            else
            {
                ShowVerifyResult(false, "❌ " + (result.Error ?? "Connexion échouée."), null);
            }
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
            UpdateResultText.Text = "Recherche en cours...";

            var result = await UpdateCheckService.CheckForUpdateAsync(currentVersion);
            if (_isClosed) return;

            CheckUpdateButton.IsEnabled = true;

            if (!string.IsNullOrEmpty(result.Error))
            {
                UpdateResultText.Text = $"❌ Recherche impossible : {result.Error}";
            }
            else if (result.Available)
            {
                UpdateResultText.Text = $"🎉 Une nouvelle version est disponible : v{result.LatestVersion}.";
                _latestReleaseUrl = result.ReleaseUrl;
                OpenReleaseButton.Visibility = string.IsNullOrEmpty(_latestReleaseUrl) ? Visibility.Collapsed : Visibility.Visible;
            }
            else
            {
                UpdateResultText.Text = "✅ Vous avez déjà la dernière version.";
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

            DialogResult = true;
            Close();
        }
    }
}
