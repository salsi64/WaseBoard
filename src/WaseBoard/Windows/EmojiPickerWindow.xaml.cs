using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace WaseBoard.Windows
{
    public partial class EmojiPickerWindow : Window
    {
        // Palette élargie : réactions/émotions, sons/musique, animaux, objets/symboles, nourriture...
        private static readonly string[] Palette =
        {
            "🔥", "💀", "😂", "🤣", "😱", "😭", "😡", "🤡", "😴", "🤔", "🥳", "😎",
            "😏", "🥺", "😳", "🤯", "🥶", "🤢", "🤮", "😵", "🫠", "🙄", "😬", "🥴",
            "🎉", "🎵", "🎶", "🎤", "🥁", "📢", "🚨", "⚠️", "🔔", "📣", "🎸", "🎧",
            "🎷", "🎺", "🎹", "🔊", "🔇", "📯", "🪘", "🎬",
            "👍", "👎", "👏", "🙌", "🤝", "🖕", "🤙", "✌️", "🤞", "🫡", "👊", "🙏",
            "💩", "🍆", "🍑", "🐸", "🐶", "🐱", "🦆", "🐔", "🐷", "🦉",
            "🐵", "🦧", "🐢", "🦀", "🐙", "🦈", "🐺", "🦁", "🐴", "🐭",
            "🎮", "⭐", "💥", "🌈", "👑", "🦄", "👻", "🎃", "💣", "🧨", "🚀", "⚡",
            "🛸", "🎯", "🏆", "🎲", "🃏", "🔮", "🧙", "🤖", "👽", "💎",
            "❤️", "💔", "💯", "❓", "❗", "✅", "☑️", "❌", "🔞", "🍺", "🍕", "🍔", "☕",
            "🍟", "🌭", "🍿", "🧃", "🍩", "🎂", "🍫", "🥤", "🍷", "🍾",
            "🔴", "🟠", "🟡", "🟢", "🔵", "🟣", "⚪", "⚫",
            "😅", "🤩", "👌", "🥚", "🧑", "☀️", "❄️", "💍", "🏠", "🏳️‍🌈"
        };

        /// <summary>Emoji choisi, chaîne vide pour "retirer", ou null si annulé.</summary>
        public string? Result { get; private set; }

        /// <summary>Mode "obligatoire" (voir AddSoundFiles) : masque Annuler/Retirer l'emoji et
        /// désactive Valider tant qu'aucun emoji n'est choisi — on ne peut pas ressortir de cette
        /// fenêtre sans en avoir assigné un, sauf en la fermant via la croix de la barre de titre
        /// (dans ce cas Result reste null, géré normalement par l'appelant).</summary>
        public EmojiPickerWindow(string? currentEmoji, bool required = false)
        {
            InitializeComponent();
            CustomEmojiBox.Text = currentEmoji ?? "";

            foreach (var emoji in Palette)
            {
                // Image plutôt que texte : voir EmojiImageResolver (WPF n'affiche pas en couleur
                // le format d'emoji récent de Windows 11).
                var button = new Button
                {
                    Content = new Image { Source = EmojiImageResolver.Resolve(emoji), Width = 22, Height = 22 },
                    Width = 38,
                    Height = 38,
                    Margin = new Thickness(3),
                    Background = Brushes.Transparent,
                    BorderThickness = new Thickness(0),
                    ToolTip = emoji
                };
                button.Click += (_, _) => { CustomEmojiBox.Text = emoji; };
                EmojiPalette.Children.Add(button);
            }

            if (required)
            {
                Title = "Choisissez un emoji pour ce son";
                IntroText.Text = "Choisissez un emoji pour ce son (obligatoire)";
                CancelButton.Visibility = Visibility.Collapsed;
                RemoveButton.Visibility = Visibility.Collapsed;
                OkButton.IsEnabled = false;
                CustomEmojiBox.TextChanged += (_, _) => OkButton.IsEnabled = !string.IsNullOrWhiteSpace(CustomEmojiBox.Text);
            }
        }

        private void Ok_Click(object sender, RoutedEventArgs e)
        {
            Result = CustomEmojiBox.Text.Trim();
            DialogResult = true;
            Close();
        }

        private void Remove_Click(object sender, RoutedEventArgs e)
        {
            Result = "";
            DialogResult = true;
            Close();
        }

        private void Cancel_Click(object sender, RoutedEventArgs e)
        {
            Result = null;
            DialogResult = false;
            Close();
        }
    }
}
