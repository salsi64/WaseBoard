using System.Diagnostics;
using System.Linq;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Navigation;
using WaseBoard.Models;
using WaseBoard.Services;

namespace WaseBoard.Windows
{
    /// <summary>
    /// Assistant de premier lancement (3 étapes : bienvenue → serveur → identité Discord),
    /// remplace le nudge affiché au démarrage. Réutilise directement SoundLibraryService (mêmes
    /// méthodes que SettingsWindow : GetServerStatusAsync, VerifyUserIdAsync) plutôt que de
    /// dupliquer la logique de test/vérification.
    /// </summary>
    public partial class OnboardingWindow : Window
    {
        private readonly AppSettings _settings;
        private readonly SoundLibraryService _library;
        private readonly StackPanel[] _steps;
        private int _currentStep;
        private string? _verifiedDiscordId;

        private const int DiscordStepIndex = 2;

        /// <summary>
        /// startAtDiscordStep : vrai quand l'assistant a déjà été vu (Bienvenue/Serveur déjà
        /// remplis lors d'un lancement précédent) mais que l'ID Discord, désormais obligatoire,
        /// manque encore — évite de refaire revoir les deux premières étapes à chaque lancement.
        /// </summary>
        public OnboardingWindow(AppSettings settings, SoundLibraryService library, bool startAtDiscordStep = false)
        {
            InitializeComponent();
            _settings = settings;
            _library = library;
            _steps = new[] { StepWelcome, StepServer, StepDiscord };
            _currentStep = startAtDiscordStep ? DiscordStepIndex : 0;

            ServerUrlBox.Text = _settings.ServerUrl;
            ServerTokenBox.Text = _settings.ServerToken;
            DiscordIdBox.Text = _settings.DiscordUserId;

            UpdateStepUi();
        }

        private void UpdateStepUi()
        {
            for (var i = 0; i < _steps.Length; i++)
                _steps[i].Visibility = i == _currentStep ? Visibility.Visible : Visibility.Collapsed;

            BackButton.Visibility = _currentStep > 0 ? Visibility.Visible : Visibility.Collapsed;
            NextButton.Content = _currentStep == _steps.Length - 1 ? "Terminer" : "Suivant";
            StepIndicatorText.Text = $"Étape {_currentStep + 1} sur {_steps.Length}";

            // L'ID Discord est obligatoire : impossible de "Passer" une fois sur cette étape, et
            // "Terminer" reste désactivé tant qu'aucun ID numérique n'est saisi.
            var onDiscordStep = _currentStep == DiscordStepIndex;
            SkipButton.Visibility = onDiscordStep ? Visibility.Collapsed : Visibility.Visible;
            if (onDiscordStep) UpdateDiscordNextEnabled();
            else NextButton.IsEnabled = true;
        }

        private void UpdateDiscordNextEnabled()
        {
            NextButton.IsEnabled = DiscordIdBox.Text.Any(char.IsDigit);
        }

        private void DiscordIdBox_TextChanged(object sender, TextChangedEventArgs e)
        {
            if (_currentStep == DiscordStepIndex) UpdateDiscordNextEnabled();
        }

        private void Back_Click(object sender, RoutedEventArgs e)
        {
            if (_currentStep == 0) return;
            _currentStep--;
            UpdateStepUi();
        }

        /// <summary>
        /// Applique l'URL/le jeton saisis à _settings immédiatement (pas seulement à la toute
        /// dernière étape) : la vérification de l'ID Discord (étape suivante) authentifie ses
        /// appels avec _settings.ServerToken, donc si le jeton n'est committé qu'à la fin, cette
        /// vérification échoue systématiquement (401) même avec un jeton correct.
        /// </summary>
        private void CommitServerFields()
        {
            _settings.ServerUrl = string.IsNullOrWhiteSpace(ServerUrlBox.Text) ? _settings.ServerUrl : ServerUrlBox.Text.Trim();
            _settings.ServerToken = ServerTokenBox.Text;
        }

        private void Next_Click(object sender, RoutedEventArgs e)
        {
            if (_currentStep == 1) CommitServerFields();

            if (_currentStep < _steps.Length - 1)
            {
                _currentStep++;
                UpdateStepUi();
                return;
            }

            CommitServerFields();
            var digitsOnly = _verifiedDiscordId ?? new string(DiscordIdBox.Text.Where(char.IsDigit).ToArray());
            if (digitsOnly.Length > 0) _settings.DiscordUserId = digitsOnly;

            DialogResult = true;
            Close();
        }

        private void Skip_Click(object sender, RoutedEventArgs e)
        {
            DialogResult = false;
            Close();
        }

        /// <summary>WPF n'ouvre jamais un lien tout seul (par sécurité) : il faut explicitement
        /// démarrer le navigateur par défaut du système via ShellExecute.</summary>
        private void Hyperlink_RequestNavigate(object sender, RequestNavigateEventArgs e)
        {
            Process.Start(new ProcessStartInfo(e.Uri.AbsoluteUri) { UseShellExecute = true });
            e.Handled = true;
        }

        private async void TestConnectionButton_Click(object sender, RoutedEventArgs e)
        {
            CommitServerFields();

            TestConnectionButton.IsEnabled = false;
            var (result, channel) = await _library.GetServerStatusAsync();
            TestConnectionButton.IsEnabled = true;

            ServerResultText.Text = result switch
            {
                SoundLibraryService.ServerStatusResult.Connected =>
                    $"✅ Serveur joignable. Bot connecté au salon vocal « {channel} ».",
                // Jeton accepté mais le bot ne vous voit dans aucun salon vocal en ce moment — ce
                // n'est pas un problème de jeton, juste un état normal si vous n'êtes pas connecté
                // à Discord ou dans un salon où le bot est présent.
                SoundLibraryService.ServerStatusResult.BotNotInVoice =>
                    "✅ Serveur joignable et jeton accepté — mais le bot ne vous voit dans aucun salon " +
                    "vocal en ce moment (normal si vous n'êtes pas connecté à Discord/dans un salon où le bot est présent).",
                SoundLibraryService.ServerStatusResult.Unauthorized =>
                    "❌ Jeton d'accès incorrect.",
                _ => $"❌ Serveur injoignable.\n{_library.LastErrorDetail}"
            };
        }

        private async void VerifyButton_Click(object sender, RoutedEventArgs e)
        {
            var digitsOnly = new string(DiscordIdBox.Text.Where(char.IsDigit).ToArray());
            if (digitsOnly.Length == 0)
            {
                DiscordResultText.Text = "Entrez d'abord un ID Discord (uniquement des chiffres).";
                return;
            }

            VerifyButton.IsEnabled = false;
            var (found, username, _, guildName, error) = await _library.VerifyUserIdAsync(digitsOnly);
            VerifyButton.IsEnabled = true;

            if (found)
            {
                _verifiedDiscordId = digitsOnly;
                DiscordResultText.Text = $"✅ Trouvé : {username} (sur {guildName})";
            }
            else
            {
                DiscordResultText.Text = "❌ " + (error ?? "ID introuvable.");
            }
        }
    }
}
