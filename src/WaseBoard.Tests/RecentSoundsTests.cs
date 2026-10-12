using WaseBoard.Services;

namespace WaseBoard.Tests;

public class RecentSoundsTests
{
    [Fact]
    public void Push_puts_the_sound_first()
    {
        var recent = new List<string>();
        Assert.True(RecentSounds.Push(recent, "a"));
        Assert.True(RecentSounds.Push(recent, "b"));
        Assert.Equal(new[] { "b", "a" }, recent);
    }

    [Fact]
    public void Playing_a_sound_again_moves_it_to_the_front_without_duplicating_it()
    {
        var recent = new List<string> { "c", "b", "a" };
        Assert.True(RecentSounds.Push(recent, "a"));
        Assert.Equal(new[] { "a", "c", "b" }, recent);
    }

    [Fact]
    public void Push_reports_no_change_when_the_sound_is_already_first()
    {
        var recent = new List<string> { "a", "b" };
        Assert.False(RecentSounds.Push(recent, "a"));
        Assert.Equal(new[] { "a", "b" }, recent);
    }

    [Theory]
    [InlineData("")]
    [InlineData(null)]
    public void Push_ignores_empty_ids(string? id)
    {
        var recent = new List<string> { "a" };
        Assert.False(RecentSounds.Push(recent, id!));
        Assert.Equal(new[] { "a" }, recent);
    }

    [Fact]
    public void The_stored_history_is_bounded_and_forgets_the_oldest()
    {
        var recent = new List<string>();
        for (var i = 0; i < RecentSounds.MaxStored + 10; i++) RecentSounds.Push(recent, $"s{i}");

        Assert.Equal(RecentSounds.MaxStored, recent.Count);
        Assert.Equal($"s{RecentSounds.MaxStored + 9}", recent[0]);
        Assert.DoesNotContain("s0", recent);
    }

    [Fact]
    public void Resolve_keeps_the_order_and_limits_the_count()
    {
        var recent = Enumerable.Range(0, 30).Select(i => $"s{i}").ToList();
        var shown = RecentSounds.Resolve(recent, id => id, max: 5);
        Assert.Equal(new[] { "s0", "s1", "s2", "s3", "s4" }, shown);
        Assert.Equal(RecentSounds.MaxShown, RecentSounds.Resolve(recent, id => id).Count);
    }

    [Fact]
    public void Resolve_skips_sounds_that_no_longer_exist_without_shrinking_the_section()
    {
        var recent = new List<string> { "gone1", "a", "gone2", "b", "c" };
        var catalog = new HashSet<string> { "a", "b", "c" };

        var shown = RecentSounds.Resolve(recent, id => catalog.Contains(id) ? id : null, max: 3);

        Assert.Equal(new[] { "a", "b", "c" }, shown);   // les sons supprimés n'ont pas pris de place
    }

    [Fact]
    public void Prune_removes_unknown_ids_and_counts_them()
    {
        var recent = new List<string> { "a", "gone", "b", "gone2" };
        var removed = RecentSounds.Prune(recent, id => id is "a" or "b");

        Assert.Equal(2, removed);
        Assert.Equal(new[] { "a", "b" }, recent);
    }
}
