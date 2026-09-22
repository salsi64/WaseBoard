using System.Windows;
using System.Windows.Controls;

namespace WaseBoard.Windows
{
    public partial class EmojiPickerWindow : Window
    {
        // Palette élargie : réactions/émotions, sons/musique, animaux, objets/symboles, nourriture...
        private static readonly string[] Palette =
        {
            "🔥", "💀", "😂", "🤣", "😱", "😭", "😡", "🤡", "😴", "🤔", "🥳", "😎",
            "🎉", "🎵", "🎶", "🎤", "🥁", "📢", "🚨", "⚠️", "🔔", "📣", "🎸", "🎧",
            "👍", "👎", "👏", "🙌", "🤝", "🖕", "🤙", "✌️",
            "💩", "🍆", "🍑", "🐸", "🐶", "🐱", "🦆", "🐔", "🐷", "🦉",
            "🎮", "⭐", "💥", "🌈", "👑", "🦄", "👻", "🎃", "💣", "🧨", "🚀", "⚡",
            "❤️", "💔", "💯", "❓", "❗", "✅", "❌", "🔞", "🍺", "🍕", "🍔", "☕"
        };

        /// <summary>Emoji choisi, chaîne vide pour "retirer", ou null si annulé.</summary>
        public string? Result { get; private set; }

        public EmojiPickerWindow(string? currentEmoji)
        {
            InitializeComponent();
            CustomEmojiBox.Text = currentEmoji ?? "";

            foreach (var emoji in Palette)
            {
                var button = new Button
                {
                    Content = emoji,
                    Width = 38,
                    Height = 38,
                    Margin = new Thickness(3),
                    FontSize = 18,
                    Background = System.Windows.Media.Brushes.Transparent,
                    BorderThickness = new Thickness(0)
                };
                button.Click += (_, _) => { CustomEmojiBox.Text = emoji; };
                EmojiPalette.Children.Add(button);
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
