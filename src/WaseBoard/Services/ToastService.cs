using System;

namespace WaseBoard.Services
{
    public enum ToastKind { Info, Success, Warning, Error }

    public sealed record ToastRequest(string Message, ToastKind Kind);

    /// <summary>
    /// Notifications non bloquantes affichées dans l'overlay de MainWindow — remplace les
    /// MessageBox.Show ponctuels (succès/échec d'une action) qui n'ont pas besoin d'interrompre
    /// l'utilisateur. Les fenêtres secondaires (Paramètres, découpe) n'ont pas cet overlay et
    /// utilisent AlertDialog à la place.
    /// </summary>
    public static class ToastService
    {
        public static event Action<ToastRequest>? Requested;

        public static void Show(string message, ToastKind kind = ToastKind.Info)
            => Requested?.Invoke(new ToastRequest(message, kind));
    }
}
