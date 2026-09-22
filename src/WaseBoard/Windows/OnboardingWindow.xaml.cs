using System.Linq;
using System.Windows;
using System.Windows.Controls;
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

        public OnboardingWindow(AppSettings settings, SoundLibraryService library)
        {
            InitializeComponent();
            _settings = settings;
            _library = library;
            _steps = new[] { StepWelcome, StepServer, StepDiscord };

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

        private async void TestConnectionButton_Click(object sender, RoutedEventArgs e)
        {
            CommitServerFields();

            TestConnectionButton.IsEnabled = false;
            var (connected, channel) = await _library.GetServerStatusAsync();
            TestConnectionButton.IsEnabled = true;

            if (connected)
            {
                ServerResultText.Text = $"✅ Serveur joignable. Bot connecté au salon vocal « {channel ?? "aucun"} ».";
            }
            else if (!string.IsNullOrEmpty(_library.LastErrorDetail))
            {
                // Échec réel de la requête (serveur injoignable, jeton refusé...) : le détail vient
                // directement de la réponse HTTP, contrairement au cas ci-dessous.
                ServerResultText.Text = $"❌ Serveur injoignable ou jeton incorrect.\n{_library.LastErrorDetail}";
            }
            else
            {
                // La requête a réussi (jeton accepté) mais le bot ne vous voit dans aucun salon
                // vocal en ce moment — ce n'est pas un problème de jeton, juste un état normal si
                // vous n'êtes pas connecté à un salon où le bot est présent.
                ServerResultText.Text = "✅ Serveur joignable et jeton accepté — mais le bot ne vous voit " +
                    "dans aucun salon vocal en ce moment (normal si vous n'êtes pas connecté à Discord/dans un salon où le bot est présent).";
            }
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
