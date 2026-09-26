using System;
using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Shell;
using System.Windows.Controls;

namespace WaseBoard.Windows.Chrome
{
    /// <summary>Habillage de fenêtre réutilisable : barre de titre custom + coins arrondis DWM.
    /// Activé via l'attached property Enable="True", nécessite un Border racine nommé
    /// "RootChromeBorder".</summary>
    public static class WindowChromeBehavior
    {
        public static readonly DependencyProperty EnableProperty =
            DependencyProperty.RegisterAttached("Enable", typeof(bool), typeof(WindowChromeBehavior),
                new PropertyMetadata(false, OnEnableChanged));

        public static void SetEnable(Window window, bool value) => window.SetValue(EnableProperty, value);
        public static bool GetEnable(Window window) => (bool)window.GetValue(EnableProperty);

        private const double DefaultCaptionHeight = 36;

        private static void OnEnableChanged(DependencyObject d, DependencyPropertyChangedEventArgs e)
        {
            if (d is not Window window || e.NewValue is not true) return;

            window.WindowStyle = WindowStyle.None;
            window.AllowsTransparency = false;

            WindowChrome.SetWindowChrome(window, new WindowChrome
            {
                CaptionHeight = DefaultCaptionHeight,
                GlassFrameThickness = new Thickness(0),
                ResizeBorderThickness = new Thickness(6),
                CornerRadius = new CornerRadius(0),
                UseAeroCaptionButtons = false
            });

            window.SourceInitialized += (_, _) => ApplyNativeRoundedCorners(window);
            window.Loaded += (_, _) => AttachRoundedClip(window);
        }

        private static void AttachRoundedClip(Window window)
        {
            if (window.FindName("RootChromeBorder") is not Border rootBorder) return;

            void UpdateClip()
            {
                if (rootBorder.ActualWidth <= 0 || rootBorder.ActualHeight <= 0) return;
                var radius = rootBorder.CornerRadius.TopLeft;
                rootBorder.Clip = new RectangleGeometry(
                    new Rect(0, 0, rootBorder.ActualWidth, rootBorder.ActualHeight), radius, radius);
            }

            rootBorder.SizeChanged += (_, _) => UpdateClip();
            UpdateClip();
        }

        private static void ApplyNativeRoundedCorners(Window window)
        {
            try
            {
                if (Environment.OSVersion.Version.Build < 22000) return; // Windows 11+ uniquement
                var hwnd = new WindowInteropHelper(window).Handle;
                if (hwnd == IntPtr.Zero) return;
                int preference = DWMWCP_ROUND;
                DwmSetWindowAttribute(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, ref preference, sizeof(int));
            }
            catch
            {
                // Amélioration purement visuelle : un échec (vieille build, DWM indisponible) ne
                // doit jamais empêcher la fenêtre de s'afficher.
            }
        }

        private const int DWMWA_WINDOW_CORNER_PREFERENCE = 33;
        private const int DWMWCP_ROUND = 2;

        [DllImport("dwmapi.dll")]
        private static extern int DwmSetWindowAttribute(IntPtr hwnd, int attribute, ref int pvAttribute, int cbAttribute);
    }
}
