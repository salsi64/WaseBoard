using System.Windows;
using WaseBoard.Services.Localization;

namespace WaseBoard.Windows
{
    public partial class ConfirmDialog : Window
    {
        public bool Result { get; private set; }

        public ConfirmDialog(string message, string title)
        {
            InitializeComponent();
            MessageText.Text = message;
            Title = title;
            TitleBar.TitleText = title;
        }

        private void Yes_Click(object sender, RoutedEventArgs e)
        {
            Result = true;
            DialogResult = true;
            Close();
        }

        private void No_Click(object sender, RoutedEventArgs e)
        {
            Result = false;
            DialogResult = false;
            Close();
        }

        /// <summary>Affiche une confirmation Oui/Non stylée WaseBoard. Retourne true si "Oui".</summary>
        public static bool Show(Window owner, string message, string? title = null)
        {
            var dialog = new ConfirmDialog(message, title ?? Loc.T("dialog.confirm.title")) { Owner = owner };
            dialog.ShowDialog();
            return dialog.Result;
        }
    }
}
