using Automathon.Engine;

namespace Automathon.Game.Input
{
    public abstract class InputProvider : Component
    {
        /// <summary>Desired movement direction. Its length (at most 1) is the fraction of the max speed to use.</summary>
        public abstract FixedVector2 GetMovementDir();
        /// <summary>Aiming direction (a unit vector, or zero when there's no aiming input).</summary>
        public abstract FixedVector2 GetAimingDir();
        public abstract bool ShouldShoot();
        public abstract bool ShouldShield();
        public abstract bool ShouldMissile();
        public abstract bool ShouldDash();

        /// <summary>
        /// Called by GameplayManager at the start of each frame of a match, before any entity is updated: every input
        /// provider sees the same state, the one at the end of the previous frame.
        /// </summary>
        public virtual void BeforeFrame() { }

        /// <summary>
        /// False while the next frame must wait for this input (an AI agent that hasn't answered yet). Never blocks:
        /// the game asks again on its next Unity frame. See GameplayManager.InputsReady.
        /// </summary>
        public virtual bool IsReadyForFrame() => true;
    }
}
