using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Text.RegularExpressions;

namespace WaseBoard.Services
{
    /// <summary>
    /// Les préférences que l'application partage avec la page web et l'Activité Discord : favoris, couleurs des sons, catégories personnelles
    /// et leur ordre d'affichage. C'est le document échangé avec le serveur (POST /me/preferences/sync, voir server/preferences.py). Aucun
    /// réseau ni réglage ici : seulement la forme du document, son nettoyage (mêmes limites que le serveur, sans quoi il refuserait tout)
    /// et la comparaison de deux documents.
    /// </summary>
    public sealed class PreferencesDoc
    {
        // Les limites du serveur (server/preferences.py).
        public const int FavoritesMax = 5000, ColorsMax = 5000, CategoriesMax = 200, CategoryNameMax = 100, CategorySoundsMax = 5000, OrderMax = 1000, IdMax = 200;

        private static readonly Regex HexColor = new("^#[0-9A-Fa-f]{6}$", RegexOptions.Compiled);

        /// <summary>Identifiants des sons en favoris.</summary>
        [JsonPropertyName("favorites")] public List<string> Favorites { get; set; } = new();

        /// <summary>Couleur choisie par son (identifiant → « #RRGGBB » en majuscules) ; absent = couleur automatique.</summary>
        [JsonPropertyName("colors")] public Dictionary<string, string> Colors { get; set; } = new();

        /// <summary>Catégories personnelles : nom → identifiants des sons qu'elles contiennent.</summary>
        [JsonPropertyName("categories")] public Dictionary<string, List<string>> Categories { get; set; } = new();

        /// <summary>Ordre d'affichage : noms de catégories et identifiants de serveurs Discord mélangés, comme dans l'application.</summary>
        [JsonPropertyName("order")] public List<string> Order { get; set; } = new();

        private static bool ValidId(string? value) => !string.IsNullOrEmpty(value) && value.Length <= IdMax;

        /// <summary>Document propre à partir des réglages locaux : ce que le serveur refuserait (couleur mal formée, nom trop long…) est laissé de
        /// côté, les doublons sont retirés, les noms rognés (comme le fait le serveur) et les couleurs mises en majuscules.</summary>
        public static PreferencesDoc Create(IEnumerable<string> favorites, IEnumerable<KeyValuePair<string, string>> colors,
            IEnumerable<KeyValuePair<string, List<string>>> categories, IEnumerable<string> order)
        {
            var doc = new PreferencesDoc
            {
                Favorites = favorites.Where(ValidId).Distinct().Take(FavoritesMax).ToList(),
                Order = order.Where(ValidId).Distinct().Take(OrderMax).ToList()
            };
            foreach (var (id, color) in colors)
            {
                if (doc.Colors.Count >= ColorsMax) break;
                if (ValidId(id) && color is not null && HexColor.IsMatch(color)) doc.Colors[id] = color.ToUpperInvariant();
            }
            foreach (var (rawName, ids) in categories)
            {
                var name = rawName?.Trim() ?? "";
                if (name.Length == 0 || name.Length > CategoryNameMax) continue;
                if (!doc.Categories.TryGetValue(name, out var list))
                {
                    if (doc.Categories.Count >= CategoriesMax) continue;
                    doc.Categories[name] = list = new List<string>();
                }
                foreach (var id in ids ?? new List<string>())
                    if (ValidId(id) && list.Count < CategorySoundsMax && !list.Contains(id)) list.Add(id);
            }
            return doc;
        }

        /// <summary>Mêmes préférences ? Les favoris et le contenu d'une catégorie comptent comme des ensembles (l'ordre n'y change rien) ;
        /// l'ordre d'affichage, lui, est une suite.</summary>
        public bool SemanticEquals(PreferencesDoc? other)
        {
            if (other is null) return false;
            if (!Favorites.ToHashSet().SetEquals(other.Favorites)) return false;
            if (Colors.Count != other.Colors.Count || Colors.Any(c => !other.Colors.TryGetValue(c.Key, out var v) || v != c.Value)) return false;
            if (Categories.Count != other.Categories.Count) return false;
            foreach (var (name, ids) in Categories)
                if (!other.Categories.TryGetValue(name, out var theirs) || !ids.ToHashSet().SetEquals(theirs)) return false;
            return Order.SequenceEqual(other.Order);
        }

        public string ToJson() => JsonSerializer.Serialize(this);

        /// <summary>Lit un document ; null si le texte est absent ou illisible.</summary>
        public static PreferencesDoc? FromJson(string? json)
        {
            if (string.IsNullOrWhiteSpace(json)) return null;
            try
            {
                var doc = JsonSerializer.Deserialize<PreferencesDoc>(json);
                if (doc is null) return null;
                return Create(doc.Favorites ?? new(), doc.Colors ?? new(), doc.Categories ?? new(), doc.Order ?? new());
            }
            catch (JsonException)
            {
                return null;
            }
        }
    }
}
