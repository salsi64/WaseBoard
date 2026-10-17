using System.Diagnostics;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Navigation;
using WaseBoard.Models;
using WaseBoard.Services;
using WaseBoard.Services.Localization;

namespace WaseBoard.Windows
{
    /// <summary>Pourquoi l'assistant s'ouvre — détermine l'étape de départ, le titre de la fenêtre et
    /// une bannière de contexte optionnelle (voir OnboardingWindow.DescribeReason).</summary>
    public enum OnboardingReason
    {
        /// <summary>Tout premier lancement : les 3 étapes depuis le début.</summary>
        FreshInstall,
        /// <summary>Un lien de connexion waseboard:// vient d'être cliqué (app déjà ouverte ou qui
        /// vient de démarrer) : réglages serveur déjà posés, il ne reste qu'à confirmer via Discord.</summary>
        DeepLinkReconnect,
        /// <summary>Le serveur actuellement enregistré répond 401 (jeton/adresse caduc) : direct à
        /// l'étape Serveur, avec explication (pas une saisie initiale, une correction).</summary>
        ServerReconfigNeeded,
        /// <summary>Jeton de session Discord absent/expiré, sans lien ni serveur caduc en cause —
        /// simple reconnexion à l'étape Discord.</summary>
        SessionExpired,
    }

    /// <summary>
    /// Assistant de premier lancement (3 étapes : bienvenue → serveur → connexion Discord),
    /// remplace le nudge affiché au démarrage. Réutilise directement SoundLibraryService (même
    /// méthode que SettingsWindow : GetServerStatusAsync) plutôt que de dupliquer la logique
    /// de test de connexion.
    /// </summary>
    public partial class OnboardingWindow : Window
    {
        private readonly AppSettings _settings;
        private readonly SoundLibraryService _library;
        private readonly StackPanel[] _steps;
        private int _currentStep;
        private DiscordOAuthService.LoginResult? _loginResult;

        private const int WelcomeStepIndex = 0;
        private const int ServerStepIndex = 1;
        private const int DiscordStepIndex = 2;

        private static (int Step, string Title, string? Banner) DescribeReason(OnboardingReason reason) => reason switch
        {
            OnboardingReason.DeepLinkReconnect => (DiscordStepIndex, Loc.T("onboarding.reconnect_title"),
                Loc.T("onboarding.reason.deeplink")),
            OnboardingReason.ServerReconfigNeeded => (ServerStepIndex, Loc.T("onboarding.reconnect_title"),
                Loc.T("onboarding.reason.server_changed")),
            OnboardingReason.SessionExpired => (DiscordStepIndex, Loc.T("onboarding.reconnect_title"),
                Loc.T("onboarding.reason.expired")),
            _ => (WelcomeStepIndex, Loc.T("onboarding.welcome_title"), null),
        };

        public OnboardingWindow(AppSettings settings, SoundLibraryService library, OnboardingReason reason = OnboardingReason.FreshInstall)
        {
            InitializeComponent();
            _settings = settings;
            _library = library;
            _steps = new[] { StepWelcome, StepServer, StepDiscord };

            var (step, title, banner) = DescribeReason(reason);
            _currentStep = step;
            Title = title;
            TitleBar.TitleText = title;
            if (banner is not null)
            {
                ContextBannerText.Text = banner;
                ContextBanner.Visibility = Visibility.Visible;
            }

            // Serveur par défaut intégré (voir AppIdentity.DefaultServerUrl) : l'étape Bienvenue
            // l'explique au lieu de demander une adresse/un jeton, avec une échappatoire pour qui a
            // son propre serveur. Absent en build Dev (pas de défaut) : texte d'origine inchangé.
            if (!string.IsNullOrEmpty(AppIdentity.DefaultServerUrl))
            {
                ServerModeExplainText.Text = Loc.T("onboarding.mode_default");
                UseCustomServerLink.Visibility = Visibility.Visible;
            }
            else
            {
                ServerModeExplainText.Text = Loc.T("onboarding.mode_own_server");
            }

            ServerUrlBox.Text = _settings.ServerUrl;
            ServerTokenBox.Text = _settings.ServerToken;

            UpdateStepUi();
        }

        /// <summary>Lien « Vous avez votre propre serveur ? » de l'étape Bienvenue : force l'étape
        /// Serveur même quand le défaut intégré dispenserait de la montrer.</summary>
        private void UseCustomServer_Click(object sender, RoutedEventArgs e)
        {
            _currentStep = ServerStepIndex;
            UpdateStepUi();
        }

        private bool UsesDefaultServer() =>
            !string.IsNullOrEmpty(AppIdentity.DefaultServerUrl) && _settings.ServerUrl == AppIdentity.DefaultServerUrl;

        private void UpdateStepUi()
        {
            for (var i = 0; i < _steps.Length; i++)
                _steps[i].Visibility = i == _currentStep ? Visibility.Visible : Visibility.Collapsed;

            BackButton.Visibility = _currentStep > 0 ? Visibility.Visible : Visibility.Collapsed;
            NextButton.Content = _currentStep == _steps.Length - 1 ? Loc.T("onboarding.finish") : Loc.T("onboarding.next");
            StepIndicatorText.Text = Loc.T("onboarding.step", _currentStep + 1, _steps.Length);

            // La connexion Discord est obligatoire : impossible de "Passer" une fois sur cette
            // étape, et "Terminer" reste désactivé tant que la connexion n'a pas réussi.
            var onDiscordStep = _currentStep == DiscordStepIndex;
            SkipButton.Visibility = onDiscordStep ? Visibility.Collapsed : Visibility.Visible;
            NextButton.IsEnabled = !onDiscordStep || (_loginResult?.Success == true);
        }

        private void Back_Click(object sender, RoutedEventArgs e)
        {
            if (_currentStep == WelcomeStepIndex) return;
            _currentStep--;
            UpdateStepUi();
        }

        /// <summary>
        /// Applique l'URL/le jeton saisis à _settings immédiatement (pas seulement à la toute
        /// dernière étape) : la connexion Discord (étape suivante) authentifie ses appels avec
        /// _settings.ServerToken, donc si le jeton n'est committé qu'à la fin, cette connexion
        /// échoue systématiquement (401) même avec un jeton correct.
        /// </summary>
        private void CommitServerFields()
        {
            _settings.ServerUrl = string.IsNullOrWhiteSpace(ServerUrlBox.Text) ? _settings.ServerUrl : ServerUrlBox.Text.Trim();
            _settings.ServerToken = ServerTokenBox.Text;
        }

        private void Next_Click(object sender, RoutedEventArgs e)
        {
            if (_currentStep == ServerStepIndex) CommitServerFields();

            if (_currentStep < _steps.Length - 1)
            {
                // Depuis Bienvenue, avec le serveur par défaut encore en place (pas de personnalisation,
                // pas passé par "mon propre serveur") : l'étape Serveur n'apporterait rien, direct à Discord.
                _currentStep = _currentStep == WelcomeStepIndex && UsesDefaultServer() ? DiscordStepIndex : _currentStep + 1;
                UpdateStepUi();
                return;
            }

            CommitServerFields();
            if (_loginResult is { Success: true })
            {
                _settings.DiscordSessionToken = _loginResult.SessionToken;
                _settings.DiscordUserId = _loginResult.UserId;
                _settings.DiscordUsername = _loginResult.Username;
                _settings.DiscordAvatarUrl = _loginResult.AvatarUrl;
            }

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
                    Loc.T("settings.server.test.connected", channel ?? ""),
                // Jeton accepté mais le bot ne vous voit dans aucun salon vocal en ce moment — ce
                // n'est pas un problème de jeton, juste un état normal si vous n'êtes pas connecté
                // à Discord ou dans un salon où le bot est présent.
                SoundLibraryService.ServerStatusResult.BotNotInVoice =>
                    Loc.T("onboarding.test.no_voice"),
                SoundLibraryService.ServerStatusResult.Unauthorized =>
                    Loc.T("settings.server.test.unauthorized"),
                _ => Loc.T("onboarding.test.unreachable", _library.LastErrorDetail ?? "")
            };
        }

        private async void ConnectDiscordButton_Click(object sender, RoutedEventArgs e)
        {
            ConnectDiscordButton.IsEnabled = false;
            DiscordResultBorder.Visibility = Visibility.Collapsed;
            DiscordResultText.Text = Loc.T("settings.discord.in_progress");
            DiscordResultBorder.Visibility = Visibility.Visible;
            DiscordAvatarBorder.Visibility = Visibility.Collapsed;

            var oauth = new DiscordOAuthService(_library);
            _loginResult = await oauth.LoginAsync();
            ConnectDiscordButton.IsEnabled = true;

            if (_loginResult.Success)
            {
                ConnectDiscordButton.Content = Loc.T("settings.discord.switch_account");
                DiscordResultText.Text = Loc.T("settings.discord.connected_as", _loginResult.Username ?? "");
                if (!string.IsNullOrEmpty(_loginResult.AvatarUrl))
                {
                    try
                    {
                        var image = new BitmapImage();
                        image.BeginInit();
                        image.UriSource = new System.Uri(_loginResult.AvatarUrl, System.UriKind.Absolute);
                        image.CacheOption = BitmapCacheOption.OnLoad;
                        image.EndInit();
                        DiscordAvatarBrush.ImageSource = image;
                        DiscordAvatarBorder.Visibility = Visibility.Visible;
                    }
                    catch { DiscordAvatarBorder.Visibility = Visibility.Collapsed; }
                }
            }
            else
            {
                DiscordResultText.Text = "❌ " + (_loginResult.Error ?? Loc.T("settings.discord.failed"));
            }

            if (_currentStep == DiscordStepIndex) UpdateStepUi();
        }
    }
}
