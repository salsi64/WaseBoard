using System.Windows;

namespace WaseBoard.Windows
{
    public enum AlertKind { Info, Warning, Error }

    public partial class AlertDialog : Window
    {
        public AlertDialog(string message, string title, AlertKind kind)
        {
            InitializeComponent();
            MessageText.Text = message;
            Title = title;
            TitleBar.TitleText = title;
            IconText.Text = kind switch
            {
                AlertKind.Warning => "⚠",
                AlertKind.Error => "⛔",
                _ => "ℹ"
            };
        }

        private void Ok_Click(object sender, RoutedEventArgs e) => Close();

        /// <summary>Affiche une alerte OK stylée WaseBoard, en remplacement de MessageBox.Show.</summary>
        public static void Show(Window owner, string message, string title = "WaseBoard", AlertKind kind = AlertKind.Info)
        {
            var dialog = new AlertDialog(message, title, kind) { Owner = owner };
            dialog.ShowDialog();
        }
    }
}
