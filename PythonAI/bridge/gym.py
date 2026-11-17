import zmq
from bridge.datatypes import *

class Gym:
    def __init__(self, tcp_port:str="5555"):
        self.address = "tcp://localhost:" + tcp_port
        self.context = zmq.Context()
        self.socket = None
        self.__connect__()

    def step(self, self_action: AIAction, enemy_action: AIAction, tcp_connection_timeout : None | int=500,
             frames: int = 1) -> GameState:
        """Joue `frames` frames avec ces actions (repetition d'action) et renvoie l'etat obtenu."""
        self.__send_action__(AIMessage(Reset=False, DoneWithTraining=False, SelfAction=self_action, EnemyAction=enemy_action,
                                       Frames=frames))

        return self.__wait_state__(tcp_connection_timeout, "The Headless did not answer in time (step).")

    def reset(self, tcp_connection_timeout : None | int=500, options: ResetOptions | None = None) -> GameState:
        """Demarre un nouveau match. options : graine et densite de la carte, cote de depart (voir ResetOptions)."""
        self.__send_action__(AIMessage(Reset=True, DoneWithTraining=False, SelfAction=None, EnemyAction=None,
                                       ResetOptions=options))

        return self.__wait_state__(tcp_connection_timeout, "Make sure the Headless version of the game is running before launching the python training script!")

    def end_training(self):
        self.__send_action__(AIMessage(Reset=False, DoneWithTraining=True, SelfAction=None, EnemyAction=None))

    def close(self):
        self.socket.close(linger=0)
        self.context.term()

    def __connect__(self):
        # Une socket REQ dont la requête est restée sans réponse refuse tout nouvel envoi (EFSM) :
        # après un délai dépassé, on la ferme sans attendre et on en ouvre une neuve ("Lazy Pirate").
        # La réponse tardive du Headless à l'ancienne requête est alors perdue, ce qui est voulu.
        if self.socket is not None:
            self.socket.close(linger=0)
        self.socket = self.context.socket(zmq.REQ)
        self.socket.connect(self.address)

        self.poller = zmq.Poller()
        self.poller.register(self.socket, zmq.POLLIN)

    def __wait_state__(self, tcp_connection_timeout: None | int, timeout_message: str) -> GameState:
        if self.poller.poll(tcp_connection_timeout):
            return self.__receive_state__()

        self.__connect__()
        raise TimeoutError(timeout_message)

    def __receive_state__(self):
        state_string = self.socket.recv_string()
        return GameState.model_validate_json(state_string)

    def __send_action__(self, msg: AIMessage):
        # Les champs None (regles non precisees, options absentes) ne sont pas envoyes : le jeu garde ses defauts
        s = msg.model_dump_json(exclude_none=True)
        self.socket.send_string(s)
