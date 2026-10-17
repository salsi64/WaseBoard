using System.Collections.Generic;
using System.Text.Json;

namespace WaseBoard.Services
{
    /// <summary>
    /// Décide quand les catégories personnelles doivent être envoyées au serveur. L'application reste la source : le serveur n'en garde
    /// qu'une copie, pour que la version web et l'Activité Discord puissent l'afficher. Aucun réseau ici (voir
    /// SoundLibraryService.CategoryMirror.cs) : seulement « y a-t-il quelque chose de nouveau à envoyer ? ».
    /// </summary>
    public sealed class CategoryMirror
    {
        private readonly object _lock = new();
        private string? _sent;     // dernière copie reçue par le serveur depuis le lancement ; null = rien envoyé
        private string? _target;   // serveur et compte auxquels _sent a été envoyée

        /// <summary>Corps du PUT /me/categories : les catégories (nom → identifiants de sons) et l'ordre d'affichage (noms de catégories et
        /// identifiants de serveurs mélangés).</summary>
        public static string BuildPayload(IReadOnlyDictionary<string, List<string>> categories, IReadOnlyList<string> order) =>
            JsonSerializer.Serialize(new { categories, order });

        /// <summary>
        /// Le corps à envoyer, ou null s'il n'y a rien à envoyer : la copie du serveur est déjà à jour, ou bien on n'a encore rien envoyé
        /// depuis le lancement et il n'y a aucune catégorie ici (un nouveau PC sans catégories n'efface pas la copie d'un autre PC).
        /// </summary>
        /// <param name="target">Identifie le serveur et le compte visés : un autre serveur ou un autre compte reçoit la copie en entier.</param>
        public string? PayloadToSend(string target, IReadOnlyDictionary<string, List<string>> categories, IReadOnlyList<string> order)
        {
            var payload = BuildPayload(categories, order);
            lock (_lock)
            {
                if (target != _target)
                {
                    _target = target;
                    _sent = null;
                }
                if (payload == _sent) return null;
                if (_sent is null && categories.Count == 0) return null;
                return payload;
            }
        }

        /// <summary>Le serveur a reçu cette copie. Ignoré si la cible a changé entre-temps (réponse tardive d'un ancien serveur).</summary>
        public void MarkSent(string target, string payload)
        {
            lock (_lock)
            {
                if (target == _target) _sent = payload;
            }
        }
    }
}
