using System;
using System.Collections.Generic;
using System.Linq;
using System.Windows.Media;

namespace WaseBoard.Services
{
    /// <summary>Une palette de l'interface : fond, panneaux, pistes (sliders), texte et accent.</summary>
    public sealed record PalettePreset(string Id, string Name, Color Bg, Color Panel, Color Track, Color Text, Color Accent);

    /// <summary>Palettes proposées dans Paramètres > Apparence. La palette claire (thème Windows clair)
    /// est commune ; seul son accent change d'un preset à l'autre.</summary>
    public static class PalettePresets
    {
        public const string DefaultId = "NightBlue";

        private static Color Hex(string hex) => (Color)ColorConverter.ConvertFromString(hex);

        public static readonly IReadOnlyList<PalettePreset> All = new[]
        {
            new PalettePreset("NightBlue", "Bleu nuit", Hex("#0F1624"), Hex("#182236"), Hex("#26324A"), Hex("#EAF0FA"), Hex("#38BDF8")),
            new PalettePreset("Violet", "Violet", Hex("#1E1E2E"), Hex("#2A2A3C"), Hex("#3A3A50"), Hex("#F2F2F7"), Hex("#7C5CFF")),
            new PalettePreset("Mint", "Menthe", Hex("#101714"), Hex("#1A2420"), Hex("#2A3A33"), Hex("#EBF5F0"), Hex("#34D399")),
            new PalettePreset("Rose", "Rose braise", Hex("#1A1419"), Hex("#261D25"), Hex("#3A2B38"), Hex("#F7EEF3"), Hex("#FB7185")),
            new PalettePreset("Amber", "Ambre", Hex("#17140F"), Hex("#241F16"), Hex("#3A3326"), Hex("#F7F2E8"), Hex("#F59E0B")),
        };

        public static PalettePreset Get(string? id) =>
            All.FirstOrDefault(p => p.Id == id) ?? All.First(p => p.Id == DefaultId);

        // Palette claire commune (thème Windows clair)
        public static readonly Color LightBg = Hex("#F5F7FB");
        public static readonly Color LightPanel = Colors.White;
        public static readonly Color LightTrack = Hex("#E0E4EC");
        public static readonly Color LightText = Hex("#16202F");

        /// <summary>Éclaircit une couleur vers le blanc (fond personnalisé → couleur de panneau dérivée).</summary>
        public static Color Lighten(Color color, double amount) => Color.FromRgb(
            (byte)Math.Round(color.R + (255 - color.R) * amount),
            (byte)Math.Round(color.G + (255 - color.G) * amount),
            (byte)Math.Round(color.B + (255 - color.B) * amount));

        /// <summary>Décale la teinte d'une couleur (en degrés) en gardant saturation et luminosité : sert à dériver, de
        /// l'accent de la palette, une couleur voisine pour les dégradés (accent → accent proche).</summary>
        public static Color ShiftHue(Color color, double degrees)
        {
            double r = color.R / 255.0, g = color.G / 255.0, b = color.B / 255.0;
            double max = Math.Max(r, Math.Max(g, b)), min = Math.Min(r, Math.Min(g, b)), d = max - min;
            double h = d == 0 ? 0
                : max == r ? 60 * (((g - b) / d) % 6)
                : max == g ? 60 * ((b - r) / d + 2)
                : 60 * ((r - g) / d + 4);
            double s = max == 0 ? 0 : d / max, v = max;

            h = ((h + degrees) % 360 + 360) % 360;
            double c = v * s, x = c * (1 - Math.Abs((h / 60) % 2 - 1)), m = v - c;
            var (r1, g1, b1) = h < 60 ? (c, x, 0.0) : h < 120 ? (x, c, 0.0) : h < 180 ? (0.0, c, x)
                : h < 240 ? (0.0, x, c) : h < 300 ? (x, 0.0, c) : (c, 0.0, x);
            return Color.FromRgb((byte)Math.Round((r1 + m) * 255), (byte)Math.Round((g1 + m) * 255), (byte)Math.Round((b1 + m) * 255));
        }

        /// <summary>Couleur de texte lisible posé sur un fond d'accent : sombre sur un accent clair, blanche sur un accent foncé.</summary>
        public static Color OnAccent(Color accent)
        {
            // Luminance relative (sRGB) approchée : suffisante pour choisir entre texte sombre et blanc.
            var luminance = (0.2126 * accent.R + 0.7152 * accent.G + 0.0722 * accent.B) / 255.0;
            return luminance > 0.55 ? Color.FromRgb(0x0B, 0x12, 0x20) : Colors.White;
        }
    }
}
