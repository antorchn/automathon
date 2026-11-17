using Automathon.Engine;
using Automathon.Game.View;
using System;
using UnityEngine;

namespace Automathon.Game
{
    public abstract class EntityView : MonoBehaviour
    {
        public abstract Type EntityType { get; }
        public abstract void Initialize(Entity entity);
    }

    public class EntityView<T> : EntityView where T : Entity
    {
        public T Entity { get; private set; }
        public override Type EntityType => typeof(T);

        // End-of-game slow motion (see SlowMotion): the velocity is measured from the entity's last moves, so that the
        // view can keep drifting the same way once it stops following the entity
        private Vector2 lastPosition, velocity;   // world units per second
        private float lastAngle, angularVelocity; // degrees per second
        private bool hasLastPose;
        private bool entityDestroyedDuringSlowMotion;

        public sealed override void Initialize(Entity entity) => Initialize((T)entity);
        public virtual void Initialize(T entity)
        {
            Entity = entity;
            entity.Destroyed += HandleControllerDestroyed;
            SlowMotion.Ended += OnSlowMotionEnded;
        }

        protected virtual void LateUpdate()
        {
            if (SlowMotion.IsActive)
            {
                // Time.deltaTime is scaled by Time.timeScale, which is what slows the drift down
                transform.position += (Vector3)(velocity * Time.deltaTime);
                transform.rotation *= Quaternion.Euler(0f, 0f, angularVelocity * Time.deltaTime);
                return;
            }

            Vector2 position = Entity.Position.ToVector2();
            Quaternion rotation = ViewMath.RadRotationToQuaternion(Entity.Rotation);
            float angle = rotation.eulerAngles.z;
            if (hasLastPose)
            {
                // the simulation does exactly one step per rendered frame
                velocity = (position - lastPosition) * GameplayConstants.FRAMERATE;
                angularVelocity = Mathf.DeltaAngle(lastAngle, angle) * GameplayConstants.FRAMERATE;
            }
            lastPosition = position;
            lastAngle = angle;
            hasLastPose = true;

            transform.SetPositionAndRotation(position, rotation);
        }

        private void HandleControllerDestroyed()
        {
            // During the slow motion the view stays on screen, drifting, until the whole arena disappears under the light
            if (SlowMotion.IsActive)
            {
                entityDestroyedDuringSlowMotion = true;
                return;
            }

            OnControllerDestroyed();
        }

        private void OnSlowMotionEnded()
        {
            // Silently: OnControllerDestroyed would play destruction effects (missile explosion, sounds...) nobody should get
            if (entityDestroyedDuringSlowMotion)
                Destroy(gameObject);
        }

        protected virtual void OnControllerDestroyed()
        {
            Destroy(gameObject);
        }

        protected virtual void OnDestroy()
        {
            if (Entity != null)
                Entity.Destroyed -= HandleControllerDestroyed;
            SlowMotion.Ended -= OnSlowMotionEnded;
        }
    }
}

