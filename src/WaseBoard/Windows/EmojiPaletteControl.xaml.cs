using System;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace WaseBoard.Windows
{
    /// <summary>Grille d'emojis cliquables : lève EmojiChosen avec l'emoji choisi. Utilisée par
    /// EmojiPickerWindow et par TrimWindow (choix de l'emoji directement dans la fenêtre de découpe).</summary>
    public partial class EmojiPaletteControl : UserControl
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

        public event Action<string>? EmojiChosen;

        private static Image CreateEmojiImage(string emoji)
        {
            var image = new Image { Source = EmojiImageResolver.Resolve(emoji), Width = 28, Height = 28 };
            RenderOptions.SetBitmapScalingMode(image, BitmapScalingMode.HighQuality);
            return image;
        }

        public EmojiPaletteControl()
        {
            InitializeComponent();

            foreach (var emoji in Palette)
            {
                // Image plutôt que texte : voir EmojiImageResolver (WPF n'affiche pas en couleur
                // le format d'emoji récent de Windows 11).
                var button = new Button
                {
                    Content = CreateEmojiImage(emoji),
                    Width = 38,
                    Height = 38,
                    Margin = new Thickness(2),
                    Background = Brushes.Transparent,
                    BorderThickness = new Thickness(0),
                    ToolTip = emoji
                };
                button.Click += (_, _) => EmojiChosen?.Invoke(emoji);
                EmojiPalette.Children.Add(button);
            }
        }
    }
}
