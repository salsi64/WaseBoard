using System.Text.Json;
using WaseBoard.Services;

namespace WaseBoard.Tests;

public class CategoryMirrorTests
{
    private const string Server = "https://waseboard.test|20";

    private static Dictionary<string, List<string>> Cats(params (string Name, string[] Ids)[] items) =>
        items.ToDictionary(i => i.Name, i => i.Ids.ToList());

    [Fact]
    public void Le_corps_contient_les_categories_et_l_ordre_tels_quels()
    {
        var payload = CategoryMirror.BuildPayload(Cats(("Mèmes", new[] { "a", "b" }), ("Jeux", new[] { "c" })), new[] { "Mèmes", "3101", "Jeux" });
        using var doc = JsonDocument.Parse(payload);
        var categories = doc.RootElement.GetProperty("categories");
        Assert.Equal(new[] { "a", "b" }, categories.GetProperty("Mèmes").EnumerateArray().Select(e => e.GetString()).ToArray());
        Assert.Equal(new[] { "c" }, categories.GetProperty("Jeux").EnumerateArray().Select(e => e.GetString()).ToArray());
        Assert.Equal(new[] { "Mèmes", "3101", "Jeux" }, doc.RootElement.GetProperty("order").EnumerateArray().Select(e => e.GetString()).ToArray());
    }

    [Fact]
    public void Au_premier_passage_les_categories_sont_envoyees()
    {
        var mirror = new CategoryMirror();
        Assert.NotNull(mirror.PayloadToSend(Server, Cats(("A", new[] { "x" })), new[] { "A" }));
    }

    [Fact]
    public void Une_copie_deja_recue_n_est_pas_renvoyee_mais_un_changement_oui()
    {
        var mirror = new CategoryMirror();
        var categories = Cats(("A", new[] { "x" }));
        var payload = mirror.PayloadToSend(Server, categories, new[] { "A" })!;
        Assert.NotNull(mirror.PayloadToSend(Server, categories, new[] { "A" }));   // pas encore confirmée par le serveur : on la propose encore
        mirror.MarkSent(Server, payload);
        Assert.Null(mirror.PayloadToSend(Server, categories, new[] { "A" }));

        categories["A"].Add("y");
        var changed = mirror.PayloadToSend(Server, categories, new[] { "A" });
        Assert.NotNull(changed);
        Assert.NotEqual(payload, changed);
        Assert.NotNull(mirror.PayloadToSend(Server, categories, new[] { "A", "B" }));   // un autre ordre change aussi la copie
    }

    [Fact]
    public void Un_nouveau_PC_sans_categories_n_efface_pas_la_copie_d_un_autre()
    {
        var mirror = new CategoryMirror();
        Assert.Null(mirror.PayloadToSend(Server, Cats(), new[] { "3101", "3102" }));
    }

    [Fact]
    public void Supprimer_la_derniere_categorie_vide_bien_la_copie_du_serveur()
    {
        var mirror = new CategoryMirror();
        var categories = Cats(("A", new[] { "x" }));
        mirror.MarkSent(Server, mirror.PayloadToSend(Server, categories, new[] { "A" })!);

        categories.Clear();
        var payload = mirror.PayloadToSend(Server, categories, new[] { "3101" });
        Assert.NotNull(payload);
        using var doc = JsonDocument.Parse(payload!);
        Assert.Empty(doc.RootElement.GetProperty("categories").EnumerateObject());
    }

    [Fact]
    public void Un_autre_serveur_ou_un_autre_compte_recoit_la_copie_en_entier()
    {
        var mirror = new CategoryMirror();
        var categories = Cats(("A", new[] { "x" }));
        mirror.MarkSent(Server, mirror.PayloadToSend(Server, categories, new[] { "A" })!);
        Assert.Null(mirror.PayloadToSend(Server, categories, new[] { "A" }));

        Assert.NotNull(mirror.PayloadToSend("https://autre.test|20", categories, new[] { "A" }));
        Assert.NotNull(mirror.PayloadToSend(Server, categories, new[] { "A" }));   // et de retour sur le premier : à renvoyer, il n'a pas été mis à jour entre-temps
    }

    [Fact]
    public void Une_reponse_tardive_d_un_ancien_serveur_est_ignoree()
    {
        var mirror = new CategoryMirror();
        var categories = Cats(("A", new[] { "x" }));
        var payload = mirror.PayloadToSend(Server, categories, new[] { "A" })!;
        mirror.PayloadToSend("https://autre.test|20", categories, new[] { "A" });   // on a changé de serveur pendant l'envoi
        mirror.MarkSent(Server, payload);
        Assert.NotNull(mirror.PayloadToSend("https://autre.test|20", categories, new[] { "A" }));
    }
}
