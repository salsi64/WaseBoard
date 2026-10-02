using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace WaseBoard.Windows
{
    public partial class EmojiPickerWindow : Window
    {
        /// <summary>Emoji choisi, chaîne vide pour "retirer", ou null si annulé.</summary>
        public string? Result { get; private set; }

        public EmojiPickerWindow(string? currentEmoji)
        {
            InitializeComponent();
            CustomEmojiBox.Text = currentEmoji ?? "";

            Palette.EmojiChosen += emoji => CustomEmojiBox.Text = emoji;
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
