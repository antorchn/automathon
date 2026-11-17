using Automathon.AI;
using Automathon.Engine;
using Newtonsoft.Json;
using System;
using System.IO;

namespace HeadlessBridge;

public static class Program
{
    private static TrainingManager gameBridge;

    /// <summary>
    /// Headless [tcp://localhost:5555]        training server (ZeroMQ REP, one request = one step)
    /// Headless --replay match.json [...]    plays recorded matches (PythonAI/webplay/replay.py) without display, prints
    ///                                        how each one ends and checks it against the end recorded in the file
    ///                                        (exit code 1 if one differs): the replay the browser plays is the same match.
    /// </summary>
    public static int Main(string[] args)
    {
        if (args.Length > 0 && args[0] == "--replay")
            return CheckReplays(args[1..]);

        string tcpAddress = args.Length == 0 ? "tcp://localhost:5555" : args[0];

        gameBridge = new TrainingManager(tcpAddress);

        while (!gameBridge.Step()) { }

        gameBridge.Dispose();
        return 0;
    }

    private static int CheckReplays(string[] files)
    {
        if (files.Length == 0)
        {
            Console.Error.WriteLine("Usage: Headless --replay match.json [other.json ...]");
            return 2;
        }

        GameplayManager.Initialize();
        int failures = 0;
        foreach (string file in files)
        {
            MatchReplay replay = JsonConvert.DeserializeObject<MatchReplay>(File.ReadAllText(file));
            ReplayResult result = replay.Run();
            string verdict = replay.Result == null ? "no recorded end to compare"
                           : result.SameAs(replay.Result) ? "OK, same as recorded"
                           : $"DIFFERENT from the recorded end ({replay.Result})";
            if (replay.Result != null && !result.SameAs(replay.Result))
                failures++;
            Console.WriteLine($"{file}: {result} -> {verdict}");
        }
        GameplayManager.Dispose();
        return failures == 0 ? 0 : 1;
    }
}
