using System.Linq;
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

        public SettingsWindow(AppSettings settings, SoundLibraryService library)
        {
            InitializeComponent();
            _settings = settings;
            _library = library;

            ServerUrlBox.Text = _settings.ServerUrl;
            ServerTokenBox.Text = _settings.ServerToken;
            ManualUserIdBox.Text = _settings.DiscordUserId;

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
            var (connected, channel) = await _library.GetServerStatusAsync();
            TestConnectionButton.IsEnabled = true;

            var message = connected
                ? $"Serveur joignable. Bot connecté au salon vocal « {channel ?? "aucun (utilisez /join dans Discord)"} »."
                : "Serveur injoignable, jeton incorrect, ou vous n'êtes actuellement dans aucun salon où le bot est présent." +
                  (string.IsNullOrEmpty(_library.LastErrorDetail) ? "" : "\nDétail : " + _library.LastErrorDetail);

            AlertDialog.Show(this, message, "Serveur WaseBoard",
                connected ? AlertKind.Info : AlertKind.Warning);

            _settings.ServerUrl = previousUrl;
            _settings.ServerToken = previousToken;
        }

        private async void VerifyUserIdButton_Click(object sender, RoutedEventArgs e)
        {
            var digitsOnly = new string(ManualUserIdBox.Text.Where(char.IsDigit).ToArray());
            if (digitsOnly.Length == 0)
            {
                ShowVerifyResult(false, "Entrez d'abord un ID Discord (uniquement des chiffres).", null);
                return;
            }

            // Applique temporairement l'URL/le jeton actuellement saisis (comme "Tester la
            // connexion") : sinon, tant que "Enregistrer" n'a pas été cliqué, cette vérification
            // utilise l'ancien jeton enregistré (potentiellement vide) et échoue en 401 même avec
            // un jeton correct fraîchement tapé.
            var previousUrl = _settings.ServerUrl;
            var previousToken = _settings.ServerToken;
            _settings.ServerUrl = string.IsNullOrWhiteSpace(ServerUrlBox.Text) ? previousUrl : ServerUrlBox.Text.Trim();
            _settings.ServerToken = ServerTokenBox.Text;

            VerifyUserIdButton.IsEnabled = false;
            VerifyResultBorder.Visibility = Visibility.Collapsed;

            var (found, username, avatarUrl, guildName, error) = await _library.VerifyUserIdAsync(digitsOnly);

            VerifyUserIdButton.IsEnabled = true;
            _settings.ServerUrl = previousUrl;
            _settings.ServerToken = previousToken;

            if (found)
                ShowVerifyResult(true, $"✅ Trouvé : {username} (sur {guildName})", avatarUrl);
            else
                ShowVerifyResult(false, "❌ " + (error ?? "ID introuvable."), null);
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

        private void SaveButton_Click(object sender, RoutedEventArgs e)
        {
            _settings.ServerUrl = string.IsNullOrWhiteSpace(ServerUrlBox.Text)
                ? "http://VOTRE_IP:5005" : ServerUrlBox.Text.Trim();
            _settings.ServerToken = ServerTokenBox.Text;
            _settings.LocalPlaybackVolume = (float)LocalVolumeSlider.Value;
            _settings.BackgroundColorHex = _selectedBgColorHex;
            _settings.UiTheme = _selectedTheme;
            _settings.FollowSystemTheme = FollowSystemThemeCheckBox.IsChecked == true;
            _settings.FollowSystemAccent = FollowSystemAccentCheckBox.IsChecked == true;

            // On ne garde que les chiffres du champ collé : un copier-coller depuis Discord ou un
            // gestionnaire de presse-papiers peut ajouter des espaces ou des caractères invisibles
            // que .Trim() seul ne retire pas, ce qui faisait échouer la validation silencieusement.
            var digitsOnly = new string(ManualUserIdBox.Text.Where(char.IsDigit).ToArray());

            if (digitsOnly.Length > 0)
            {
                _settings.DiscordUserId = digitsOnly;
                _settings.DiscordUsername = null;
                _settings.DiscordAvatarUrl = null;
            }
            else if (!string.IsNullOrWhiteSpace(ManualUserIdBox.Text))
            {
                AlertDialog.Show(this, "L'ID Discord doit être un nombre (ex: 123456789012345678).",
                    "WaseBoard", AlertKind.Warning);
                return;
            }
            else
            {
                _settings.DiscordUserId = null;
            }

            DialogResult = true;
            Close();
        }
    }
}
