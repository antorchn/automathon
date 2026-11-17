namespace Automathon
{
    public static class GameplayConstants
    {
        public const int FRAMERATE = 60;
        public const int MAX_PLAYERS = 2;

        /// <summary>
        /// In a match, AI agents decide once every AI_DECISION_INTERVAL frames (60 / 4 = 15 Hz), on the same frames for
        /// both tanks, and keep their action in between (see AIInputProvider.BeforeFrame). Matches the frame skip of
        /// training. Human players are not limited. Python side: bridge.datatypes.DECISION_INTERVAL (same value).
        /// </summary>
        public const int AI_DECISION_INTERVAL = 4;

        /// <summary>Half size of the arena: inner face of the invisible borders (RandomMapGenerator.BorderInnerX / Y).
        /// Python side: bridge.datatypes.ARENA_HALF_WIDTH / ARENA_HALF_HEIGHT.</summary>
        public static readonly Fixed ARENA_HALF_WIDTH = Fixed.Ratio(13700, 1000);
        public static readonly Fixed ARENA_HALF_HEIGHT = Fixed.Ratio(7700, 1000);

        /// <summary>Duration of a simulation frame, in seconds.</summary>
        public static readonly Fixed DELTA_TIME = Fixed.Ratio(1, FRAMERATE);

        /// <summary>Number of simulation frames needed to cover the given duration (rounded to the nearest frame).</summary>
        public static int SecondsToFrames(Fixed seconds) => (seconds * FRAMERATE).RoundToInt();

        /// <summary>Duration in seconds of the given number of simulation frames.</summary>
        public static Fixed FramesToSeconds(int frames) => Fixed.Ratio(frames, FRAMERATE);
    }
}
