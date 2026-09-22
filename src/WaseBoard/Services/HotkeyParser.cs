using System;
using System.Linq;
using System.Windows.Input;

namespace WaseBoard.Services
{
    public static class HotkeyParser
    {
        /// <summary>Convertit une combinaison (ex: Ctrl+Alt+F1) en texte affichable et stockable.</summary>
        public static string Format(ModifierKeys modifiers, Key key)
        {
            var parts = new System.Collections.Generic.List<string>();
            if (modifiers.HasFlag(ModifierKeys.Control)) parts.Add("Ctrl");
            if (modifiers.HasFlag(ModifierKeys.Alt)) parts.Add("Alt");
            if (modifiers.HasFlag(ModifierKeys.Shift)) parts.Add("Shift");
            if (modifiers.HasFlag(ModifierKeys.Windows)) parts.Add("Win");
            parts.Add(key.ToString());
            return string.Join("+", parts);
        }

        public static bool TryParse(string text, out ModifierKeys modifiers, out Key key)
        {
            modifiers = ModifierKeys.None;
            key = Key.None;

            if (string.IsNullOrWhiteSpace(text)) return false;

            var parts = text.Split('+', StringSplitOptions.TrimEntries | StringSplitOptions.RemoveEmptyEntries);
            if (parts.Length == 0) return false;

            var keyPart = parts[^1];
            if (!Enum.TryParse<Key>(keyPart, ignoreCase: true, out key))
                return false;

            foreach (var mod in parts.Take(parts.Length - 1))
            {
                modifiers |= mod.ToLowerInvariant() switch
                {
                    "ctrl" or "control" => ModifierKeys.Control,
                    "alt" => ModifierKeys.Alt,
                    "shift" => ModifierKeys.Shift,
                    "win" or "windows" => ModifierKeys.Windows,
                    _ => ModifierKeys.None
                };
            }

            return true;
        }
    }
}
