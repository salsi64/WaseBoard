using System;
using System.Collections.Generic;
using System.Linq;
using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Input;
using System.Windows.Interop;

namespace WaseBoard.Services
{
    /// <summary>
    /// Permet de déclencher un son au clavier même quand la fenêtre n'a pas le focus
    /// (utile pendant un stream ou un appel Discord/Zoom en arrière-plan).
    /// </summary>
    public class GlobalHotkeyManager : IDisposable
    {
        private const int WM_HOTKEY = 0x0312;
        private readonly HwndSource _source;
        private readonly Dictionary<int, Action> _callbacks = new();
        private int _nextId = 9000;

        [DllImport("user32.dll")]
        private static extern bool RegisterHotKey(IntPtr hWnd, int id, uint fsModifiers, uint vk);

        [DllImport("user32.dll")]
        private static extern bool UnregisterHotKey(IntPtr hWnd, int id);

        public GlobalHotkeyManager(Window window)
        {
            var helper = new WindowInteropHelper(window);
            if (helper.Handle == IntPtr.Zero)
                throw new InvalidOperationException("La fenêtre doit être initialisée (Loaded) avant d'enregistrer des raccourcis.");

            _source = HwndSource.FromHwnd(helper.Handle)!;
            _source.AddHook(HwndHook);
        }

        /// <summary>Enregistre un raccourci. Retourne l'ID (à conserver pour pouvoir le désinscrire).</summary>
        public int? Register(ModifierKeys modifiers, Key key, Action callback)
        {
            int id = _nextId++;
            uint vk = (uint)KeyInterop.VirtualKeyFromKey(key);

            if (!RegisterHotKey(_source.Handle, id, (uint)modifiers, vk))
                return null; // déjà pris par une autre application

            _callbacks[id] = callback;
            return id;
        }

        public void Unregister(int id)
        {
            UnregisterHotKey(_source.Handle, id);
            _callbacks.Remove(id);
        }

        public void UnregisterAll()
        {
            foreach (var id in _callbacks.Keys.ToList())
                Unregister(id);
        }

        private IntPtr HwndHook(IntPtr hwnd, int msg, IntPtr wParam, IntPtr lParam, ref bool handled)
        {
            if (msg == WM_HOTKEY && _callbacks.TryGetValue(wParam.ToInt32(), out var callback))
            {
                callback();
                handled = true;
            }
            return IntPtr.Zero;
        }

        public void Dispose()
        {
            UnregisterAll();
            _source.RemoveHook(HwndHook);
        }
    }
}
