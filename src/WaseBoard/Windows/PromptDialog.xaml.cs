using System.Windows;

namespace WaseBoard.Windows
{
    public partial class PromptDialog : Window
    {
        public string? Result { get; private set; }

        public PromptDialog(string prompt, string defaultValue)
        {
            InitializeComponent();
            PromptLabel.Text = prompt;
            InputBox.Text = defaultValue;
            InputBox.SelectAll();
            Loaded += (_, _) => InputBox.Focus();
        }

        private void Ok_Click(object sender, RoutedEventArgs e)
        {
            Result = InputBox.Text;
            DialogResult = true;
            Close();
        }

        private void Cancel_Click(object sender, RoutedEventArgs e)
        {
            DialogResult = false;
            Close();
        }

        /// <summary>Affiche la boîte de dialogue et retourne le texte saisi, ou null si annulé.</summary>
        public static string? Show(Window owner, string prompt, string defaultValue = "")
        {
            var dialog = new PromptDialog(prompt, defaultValue) { Owner = owner };
            return dialog.ShowDialog() == true ? dialog.Result : null;
        }
    }
}
