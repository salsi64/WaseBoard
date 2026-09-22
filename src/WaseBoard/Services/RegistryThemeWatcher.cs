using System.Windows.Media;
using Microsoft.Win32;

namespace WaseBoard.Services
{
    public sealed record SystemThemeSnapshot(bool IsLightTheme, Color AccentColor);

    /// <summary>
    /// Lit le thème clair/sombre et la couleur d'accent de Windows depuis le registre. Conçu pour
    /// être interrogé par un DispatcherTimer côté appelant (cohérent avec les timers
    /// d'activité/statut vocal déjà présents dans MainWindow) plutôt que via
    /// SystemEvents.UserPreferenceChanged, pour ne pas ajouter de dépendance à
    /// System.Windows.Forms pour un seul événement.
    /// </summary>
    public static class RegistryThemeWatcher
    {
        private static readonly Color FallbackAccent = Color.FromRgb(0x7C, 0x5C, 0xFF);

        public static SystemThemeSnapshot ReadCurrent()
        {
            var isLight = ReadDword(
                @"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize", "AppsUseLightTheme", 1) != 0;
            return new SystemThemeSnapshot(isLight, ReadAccentColor());
        }

        private static Color ReadAccentColor()
        {
            // DWM\AccentColor est un DWORD ABGR (0xAABBGGRR) : on ignore le canal alpha.
            var raw = ReadDword(@"Software\Microsoft\Windows\DWM", "AccentColor", int.MinValue);
            if (raw == int.MinValue) return FallbackAccent;

            var value = unchecked((uint)raw);
            var r = (byte)(value & 0xFF);
            var g = (byte)((value >> 8) & 0xFF);
            var b = (byte)((value >> 16) & 0xFF);
            return Color.FromRgb(r, g, b);
        }

        private static int ReadDword(string subKey, string valueName, int fallback)
        {
            try
            {
                using var key = Registry.CurrentUser.OpenSubKey(subKey);
                if (key?.GetValue(valueName) is int value) return value;
            }
            catch
            {
                // Lecture registre best-effort : clé absente, permissions... on retombe sur le défaut.
            }
            return fallback;
        }
    }
}
