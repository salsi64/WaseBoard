using System;
using System.Linq;
using System.Diagnostics;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using WaseBoard.Models;
using WaseBoard.Services;

namespace WaseBoard.Windows
{
    public partial class SettingsWindow : Window
    {
        private readonly AppSettings _settings;
        private readonly SoundLibraryService _library;
        private string? _selectedBgColorHex;
        private string _selectedTheme = "Classic";
        private string? _latestReleaseUrl;

        // Les boutons "Tester"/"Vérifier"/"Rechercher une mise à jour" lancent un appel réseau
        // (async void) puis touchent l'UI une fois la réponse reçue ; si l'utilisateur ferme cette
        // fenêtre pendant l'attente (ex: réponse lente, ou serveur injoignable), la continuation
        // reprend sur une fenêtre déjà fermée — AlertDialog.Show(this, ...) plante alors
        // ("Owner sur une fenêtre fermée"). Ce drapeau permet d'abandonner proprement.
        private bool _isClosed;

        public SettingsWindow(AppSettings settings, SoundLibraryService library, string initialPage = "Server")
        {
            InitializeComponent();
            _settings = settings;
            _library = library;
            Closed += (_, _) => _isClosed = true;

            ServerUrlBox.Text = _settings.ServerUrl;
            ServerTokenBox.Text = _settings.ServerToken;

            if (!string.IsNullOrEmpty(_settings.DiscordSessionToken))
            {
                ConnectDiscordButton.Content = "Changer de compte";
                ShowVerifyResult(true, $"✅ Connecté en tant que {_settings.DiscordUsername}", _settings.DiscordAvatarUrl);
            }

            LocalVolumeSlider.Value = _settings.LocalPlaybackVolume;
            UpdateVolumeLabel(LocalVolumeLabel, _settings.LocalPlaybackVolume);

            _selectedBgColorHex = _settings.BackgroundColorHex ?? "#1E1E2E";
            CustomColorBox.Text = _selectedBgColorHex;
            UpdateColorPreview();

            _selectedTheme = _settings.UiTheme;
            UpdateThemeButtons();

            FollowSystemThemeCheckBox.IsChecked = _settings.FollowSystemTheme;
            FollowSystemAccentCheckBox.IsChecked = _settings.FollowSystemAccent;
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
                ("Updates", PageUpdates, NavUpdatesButton),
            };

            var accent = (Brush)FindResource("AccentBrush");
            foreach (var (pageKey, page, nav) in pages)
            {
                var isSelected = pageKey == key;
                page.Visibility = isSelected ? Visibility.Visible : Visibility.Collapsed;
                nav.Background = isSelected ? accent : Brushes.Transparent;
                nav.Foreground = isSelected ? Brushes.White : (Brush)FindResource("TextBrush");
            }
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

        private void UpdateThemeButtons()
        {
            var accent = (System.Windows.Media.Brush)FindResource("AccentBrush");
            var transparent = System.Windows.Media.Brushes.Transparent;

            ClassicThemeButton.Background = _selectedTheme == "Classic" ? accent : transparent;
            ModernThemeButton.Background = _selectedTheme == "Modern" ? accent : transparent;
        }

        private void ColorSwatch_Click(object sender, MouseButtonEventArgs e)
        {
            if (sender is FrameworkElement { Tag: string hex })
            {
                _selectedBgColorHex = hex;
                CustomColorBox.Text = hex;
                UpdateColorPreview();
            }
        }

        private void ApplyCustomColor_Click(object sender, RoutedEventArgs e)
        {
            var text = CustomColorBox.Text.Trim();
            if (!text.StartsWith("#")) text = "#" + text;

            try
            {
                _ = (Color)ColorConverter.ConvertFromString(text); // valide le format avant d'accepter
                _selectedBgColorHex = text;
                UpdateColorPreview();
            }
            catch
            {
                AlertDialog.Show(this, "Couleur invalide. Utilisez un code hexadécimal, ex: #1E1E2E",
                    "WaseBoard", AlertKind.Warning);
            }
        }

        private void UpdateColorPreview()
        {
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
            _settings.BackgroundColorHex = _selectedBgColorHex;
            _settings.UiTheme = _selectedTheme;
            _settings.FollowSystemTheme = FollowSystemThemeCheckBox.IsChecked == true;
            _settings.FollowSystemAccent = FollowSystemAccentCheckBox.IsChecked == true;

            DialogResult = true;
            Close();
        }
    }
}
