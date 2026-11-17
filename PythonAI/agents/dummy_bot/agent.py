from bridge.datatypes import GameState, AIAction


class ParticipantAgent:
    """Bot inactif mais qui tire tout droit."""
    def get_action(self, state: GameState) -> AIAction:
        return AIAction(
            MovingDirection=(0, 0),
            AimingDirection=(1, 0),
            MachineGun=True,
            Missile=False,
            Shield=False,
            Dash=False
        )
