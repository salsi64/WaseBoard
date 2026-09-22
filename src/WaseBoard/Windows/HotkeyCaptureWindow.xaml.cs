using System.Windows;
using System.Windows.Input;
using WaseBoard.Services;

namespace WaseBoard.Windows
{
    public partial class HotkeyCaptureWindow : Window
    {
        public string? CapturedHotkey { get; private set; }

        public HotkeyCaptureWindow()
        {
            InitializeComponent();
        }

        private void Window_PreviewKeyDown(object sender, KeyEventArgs e)
        {
            var key = e.Key == Key.System ? e.SystemKey : e.Key;

            // On ignore les touches de modification seules : il faut au moins une touche "principale".
            if (key is Key.LeftCtrl or Key.RightCtrl or Key.LeftAlt or Key.RightAlt
                    or Key.LeftShift or Key.RightShift or Key.LWin or Key.RWin)
            {
                return;
            }

            var modifiers = Keyboard.Modifiers;
            CapturedHotkey = HotkeyParser.Format(modifiers, key);
            CapturedText.Text = CapturedHotkey;
            ConfirmButton.IsEnabled = true;

            e.Handled = true;
        }

        private void Confirm_Click(object sender, RoutedEventArgs e)
        {
            DialogResult = true;
            Close();
        }

        private void Cancel_Click(object sender, RoutedEventArgs e)
        {
            CapturedHotkey = null;
            DialogResult = false;
            Close();
        }
    }
}
