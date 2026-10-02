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

        public EmojiPaletteControl()
        {
            InitializeComponent();

            foreach (var emoji in Palette)
            {
                // Image plutôt que texte : voir EmojiImageResolver (WPF n'affiche pas en couleur
                // le format d'emoji récent de Windows 11).
                var button = new Button
                {
                    Content = new Image { Source = EmojiImageResolver.Resolve(emoji), Width = 22, Height = 22 },
                    Width = 36,
                    Height = 36,
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
