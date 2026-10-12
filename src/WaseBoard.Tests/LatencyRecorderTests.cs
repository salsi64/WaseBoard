using WaseBoard.Services;

namespace WaseBoard.Tests;

public class LatencyRecorderTests
{
    [Fact]
    public void Median_of_odd_and_even_counts()
    {
        Assert.Equal(0, LatencyRecorder.Median(Array.Empty<long>()));
        Assert.Equal(7, LatencyRecorder.Median(new long[] { 7 }));
        Assert.Equal(20, LatencyRecorder.Median(new long[] { 10, 20, 30 }));
        Assert.Equal(25, LatencyRecorder.Median(new long[] { 10, 20, 30, 40 }));
    }

    [Fact]
    public void Summarize_is_null_when_nothing_was_recorded()
    {
        Assert.Null(new LatencyRecorder().Summarize());
    }

    [Fact]
    public void Summarize_reports_median_max_and_server_median()
    {
        var recorder = new LatencyRecorder();
        recorder.Add(180, 3);
        recorder.Add(95, 2);
        recorder.Add(310, 1);
        recorder.Add(120, 2);

        var summary = recorder.Summarize()!.Value;

        Assert.Equal(4, summary.Count);
        Assert.Equal(150, summary.MedianTotalMs);   // (120 + 180) / 2
        Assert.Equal(310, summary.MaxTotalMs);
        Assert.Equal(2, summary.MedianServerMs);    // 1, 2, 2, 3
    }

    [Fact]
    public void Server_median_is_null_when_the_server_does_not_report_it()
    {
        var recorder = new LatencyRecorder();
        recorder.Add(100, null);
        recorder.Add(200, null);

        Assert.Null(recorder.Summarize()!.Value.MedianServerMs);
    }

    [Fact]
    public void Server_median_ignores_plays_without_the_field()
    {
        var recorder = new LatencyRecorder();
        recorder.Add(100, null);
        recorder.Add(110, 4);

        Assert.Equal(4, recorder.Summarize()!.Value.MedianServerMs);
    }

    [Fact]
    public void Summarize_only_looks_at_the_last_plays()
    {
        var recorder = new LatencyRecorder();
        for (var i = 0; i < 10; i++) recorder.Add(1000, null);
        for (var i = 0; i < 5; i++) recorder.Add(50, null);

        var summary = recorder.Summarize(last: 5)!.Value;

        Assert.Equal(5, summary.Count);
        Assert.Equal(50, summary.MedianTotalMs);
        Assert.Equal(50, summary.MaxTotalMs);
    }

    [Fact]
    public void Memory_is_bounded()
    {
        var recorder = new LatencyRecorder();
        for (var i = 0; i < LatencyRecorder.Capacity + 25; i++) recorder.Add(i, null);

        var samples = recorder.Snapshot();

        Assert.Equal(LatencyRecorder.Capacity, samples.Count);
        Assert.Equal(25, samples[0].TotalMs);   // les plus anciens sont oubliés
    }
}
