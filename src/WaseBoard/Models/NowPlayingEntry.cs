namespace WaseBoard.Models
{
    /// <summary>Une entrée de la barre "now playing" : un (son, utilisateur) actuellement en train
    /// de jouer, dérivée de la même activité que SoundItem.ActiveUsers (voir PollActivityAsync).</summary>
    public class NowPlayingEntry
    {
        public string SoundId { get; init; } = "";
        public string UserId { get; init; } = "";
        public string SoundName { get; init; } = "";
        public string? Emoji { get; init; }
        public string Username { get; init; } = "";
        public string? AvatarUrl { get; init; }

        /// <summary>Clé composite (son, utilisateur) utilisée pour réconcilier la collection sans la reconstruire.</summary>
        public string Key => SoundId + "|" + UserId;
    }
}
