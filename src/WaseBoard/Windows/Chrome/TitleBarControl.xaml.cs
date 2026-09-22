using System.Windows;
using System.Windows.Controls;
using System.Windows.Shell;

namespace WaseBoard.Windows.Chrome
{
    public partial class TitleBarControl : UserControl
    {
        public static readonly DependencyProperty TitleTextProperty =
            DependencyProperty.Register(nameof(TitleText), typeof(string), typeof(TitleBarControl),
                new PropertyMetadata(string.Empty));

        public static readonly DependencyProperty ShowMinMaxProperty =
            DependencyProperty.Register(nameof(ShowMinMax), typeof(bool), typeof(TitleBarControl),
                new PropertyMetadata(true, OnShowMinMaxChanged));

        public string TitleText
        {
            get => (string)GetValue(TitleTextProperty);
            set => SetValue(TitleTextProperty, value);
        }

        /// <summary>À mettre à False pour les dialogues à taille fixe (ConfirmDialog, PromptDialog...).</summary>
        public bool ShowMinMax
        {
            get => (bool)GetValue(ShowMinMaxProperty);
            set => SetValue(ShowMinMaxProperty, value);
        }

        public TitleBarControl()
        {
            InitializeComponent();
            Loaded += (_, _) =>
            {
                if (Window.GetWindow(this) is Window window)
                {
                    window.StateChanged += (_, _) => UpdateMaximizeGlyph(window);
                    UpdateMaximizeGlyph(window);
                }
            };
        }

        private void UpdateMaximizeGlyph(Window window)
        {
            MaximizeButton.Content = window.WindowState == WindowState.Maximized ? "" : "";
            MaximizeButton.ToolTip = window.WindowState == WindowState.Maximized ? "Restaurer" : "Agrandir";
        }

        private static void OnShowMinMaxChanged(DependencyObject d, DependencyPropertyChangedEventArgs e)
        {
            var control = (TitleBarControl)d;
            var visibility = (bool)e.NewValue ? Visibility.Visible : Visibility.Collapsed;
            control.MinimizeButton.Visibility = visibility;
            control.MaximizeButton.Visibility = visibility;
        }

        private void Minimize_Click(object sender, RoutedEventArgs e)
        {
            if (Window.GetWindow(this) is Window window)
                SystemCommands.MinimizeWindow(window);
        }

        private void MaximizeRestore_Click(object sender, RoutedEventArgs e)
        {
            if (Window.GetWindow(this) is not Window window) return;
            if (window.WindowState == WindowState.Maximized)
                SystemCommands.RestoreWindow(window);
            else
                SystemCommands.MaximizeWindow(window);
        }

        private void Close_Click(object sender, RoutedEventArgs e)
        {
            if (Window.GetWindow(this) is Window window)
                SystemCommands.CloseWindow(window);
        }
    }
}
