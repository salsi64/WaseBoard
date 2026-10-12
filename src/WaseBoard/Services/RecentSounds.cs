using System;
using System.Collections.Generic;
using System.Linq;

namespace WaseBoard.Services
{
    /// <summary>
    /// Historique des derniers sons joués (section « Récents »). Logique pure, sans interface : la liste est celle de
    /// AppSettings.RecentSoundIds (du plus récent au plus ancien), seule persistée ; ce que la page affiche en est déduit
    /// avec <see cref="Resolve"/>, qui ignore les sons qui n'existent plus au catalogue.
    /// </summary>
    public static class RecentSounds
    {
        /// <summary>Nombre d'identifiants gardés dans les réglages (plus que ce qui est affiché : les sons supprimés ou
        /// invisibles sont écartés à l'affichage sans vider la section).</summary>
        public const int MaxStored = 30;

        /// <summary>Nombre de sons affichés dans la section.</summary>
        public const int MaxShown = 12;

        /// <summary>Place ce son en tête de l'historique (il n'y figure jamais deux fois) et borne la taille de la liste.
        /// Renvoie vrai si la liste a changé (déjà en tête : rien à faire).</summary>
        public static bool Push(List<string> recent, string soundId)
        {
            if (string.IsNullOrEmpty(soundId)) return false;
            if (recent.Count > 0 && recent[0] == soundId) return false;

            recent.Remove(soundId);
            recent.Insert(0, soundId);
            if (recent.Count > MaxStored) recent.RemoveRange(MaxStored, recent.Count - MaxStored);
            return true;
        }

        /// <summary>Les sons à afficher : l'historique dans l'ordre, limité à <paramref name="max"/>, sans ceux que
        /// <paramref name="find"/> ne retrouve plus (null = absent du catalogue).</summary>
        public static List<T> Resolve<T>(IEnumerable<string> recent, Func<string, T?> find, int max = MaxShown) where T : class
        {
            var result = new List<T>();
            foreach (var id in recent)
            {
                if (result.Count >= max) break;
                if (find(id) is { } found) result.Add(found);
            }
            return result;
        }

        /// <summary>Retire de l'historique les identifiants que <paramref name="exists"/> ne reconnaît plus (nettoyage
        /// périodique après un rechargement du catalogue). Renvoie le nombre d'entrées retirées.</summary>
        public static int Prune(List<string> recent, Func<string, bool> exists) => recent.RemoveAll(id => !exists(id));
    }
}
