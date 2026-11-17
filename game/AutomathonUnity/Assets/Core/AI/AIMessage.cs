using Automathon.Game;

namespace Automathon.AI
{
    public struct AIMessage
    {
        public bool Reset;
        public bool DoneWithTraining;
        public AIAction SelfAction;
        public AIAction EnemyAction;

        /// <summary>Number of frames simulated with these actions (action repeat). 0 and 1 both mean one frame.
        /// The simulation stops early when a ship dies.</summary>
        public int Frames;

        /// <summary>Options of a reset, ignored otherwise. Null = random map, your ship on the left.</summary>
        public ResetOptions ResetOptions;

        /// <summary>Answer with a binary state (see BinaryProtocol) instead of JSON.</summary>
        public bool BinaryState;
    }

    public class ResetOptions
    {
        /// <summary>Map seed: the same seed always gives the same map. Null = random map.</summary>
        public int? Seed;

        /// <summary>Wall density (0 = no walls, default 0.7). Null = default density.</summary>
        public double? MapDensity;

        /// <summary>Your ship starts on the right side instead of the left one.</summary>
        public bool SelfOnRight;

        /// <summary>Rules of the match (null = default rules). Fields left out keep their default value.</summary>
        public GameRules Rules;
    }
}
