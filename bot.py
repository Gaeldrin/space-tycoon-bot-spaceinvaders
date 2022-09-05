import traceback
from pprint import pprint
from typing import Dict, Tuple
from typing import Optional

import yaml
from space_tycoon_client import ApiClient
from space_tycoon_client import Configuration
from space_tycoon_client import GameApi
from space_tycoon_client.models.credentials import Credentials
from space_tycoon_client.models.current_tick import CurrentTick
from space_tycoon_client.models.data import Data
from space_tycoon_client.models.destination import Destination
from space_tycoon_client.models.end_turn import EndTurn
from space_tycoon_client.models.move_command import MoveCommand
from space_tycoon_client.models import TradeCommand, DecommissionCommand
from space_tycoon_client.models.construct_command import ConstructCommand
from space_tycoon_client.models.attack_command import AttackCommand
from space_tycoon_client.models.player import Player
from space_tycoon_client.models.player_id import PlayerId
from space_tycoon_client.models.ship import Ship
from space_tycoon_client.models.static_data import StaticData
from space_tycoon_client.rest import ApiException

CONFIG_FILE = "config.yml"
RADIUS = 300
ATTACK_RADIUS = 30
TRADE_CENTER_TOL = 30


class ConfigException(Exception):
    pass


class Fighter:
    def __init__(self, idf):
        self.id = idf
        self.attack = False


class Game:
    def __init__(self, api_client: GameApi, config: Dict[str, str]):
        self.me: Optional[Player] = None
        self.config = config
        self.client = api_client
        self.player_id = self.login()
        self.static_data: StaticData = self.client.static_data_get()
        self.data: Data = self.client.data_get()
        self.season = self.data.current_tick.season
        self.tick = self.data.current_tick.tick

        # dynamic fleet values
        self.fighters = {}
        self.shippers_center = [0, 0]  # will be center of shippers for now
        self.threat_active = False
        self.build_finished = False

        # this part is custom logic, feel free to edit / delete
        if self.player_id not in self.data.players:
            raise Exception("Logged as non-existent player")
        self.recreate_me()
        print(f"playing as [{self.me.name}] id: {self.player_id}")

    def recreate_me(self):
        self.me: Player = self.data.players[self.player_id]

    def game_loop(self):
        while True:
            print("-" * 30)
            try:
                print(f"tick {self.tick} season {self.season}")
                self.data: Data = self.client.data_get()
                if self.data.player_id is None:
                    raise Exception("I am not correctly logged in. Bailing out")
                self.game_logic()
                current_tick: CurrentTick = self.client.end_turn_post(EndTurn(
                    tick=self.tick,
                    season=self.season
                ))
                self.tick = current_tick.tick
                self.season = current_tick.season
            except ApiException as e:
                if e.status == 403:
                    print(f"New season started or login expired: {e}")
                    break
                else:
                    raise e
            except Exception as e:
                print(f"!!! EXCEPTION !!! Game logic error {e}")
                traceback.print_exception(e)

    def _get_fighters(self, ship_class="4"):
        my_ships: Dict[Ship] = {ship_id: ship for ship_id, ship in
                                self.data.ships.items() if ship.player == self.player_id and ship.ship_class == ship_class}
        if ship_class == "4":  # sync all fighters into self.fighters
            for ship_id, ship in my_ships.items():
                if ship_id not in self.fighters:
                    self.fighters[ship_id] = Fighter(ship_id)
            for ship_id, ship in self.fighters.items():
                if ship_id not in my_ships:
                    del self.fighters[ship_id]

        return my_ships

    def _get_free_fighters(self, ship_class="4"):
        my_ships: Dict[Ship] = {ship_id: ship for ship_id, ship in self.data.ships.items()
                                if ship.player == self.player_id and ship.ship_class == ship_class and ship.command is None}
        #my_ships: Dict[Ship] = {ship_id: ship for ship_id, ship in self.data.ships.items()}

        return len(my_ships.keys()), my_ships

    def _get_enemy_ships(self, ship_class=None, ship_player=None) -> dict:
        ships: Dict[Ship] = {}
        for ship_id, ship in self.data.ships.items():
            if ship_class is not None and ship.ship_class != ship_class:
                continue
            if ship_player is not None and ship.player != ship_player:
                continue
            if ship_player is None and ship.player == self.player_id:
                continue
            ships[ship_id] = ship

        return ships

    def _get_our_mothership(self) -> dict:
        ships: Dict[Ship] = [(ship_id, ship) for ship_id, ship in
                             self.data.ships.items() if ship.player == self.player_id and ship.ship_class == "1"]

        if len(ships) == 0:
            return 0, 0
        return ships[0]

    def _get_closest_ship_to_all_fighters(self, enemy_ships, our_ships):
        ship_pos = [(ship.position[0], ship.position[1]) for ship in our_ships.values()]

        closest = 1e6
        closest_id = None
        for enemy_ship_id, enemy_ship in enemy_ships.items():
            sum_dist = 0
            for pos in ship_pos:
                sum_dist += get_dist(pos[0], pos[1], enemy_ship.position[0], enemy_ship.position[1])
            if sum_dist < closest:
                closest = sum_dist
                closest_id = enemy_ship_id

        return closest_id

    def _attack_on_the_ship(self, commands, attack_id, fighters):
        for f_id, fighter in fighters.items():
            commands[f_id] = AttackCommand(attack_id)

    def _update_shippers_center(self, ships):
        sum_x = 0
        sum_y = 0
        ship_count = len(ships.keys())
        if ship_count == 0:
            return [-10000, -10000]
        for ship in ships.values():
            sum_x += ship.position[0]
            sum_y += ship.position[1]
        self.shippers_center = [sum_x / ship_count, sum_y / ship_count]

    def initiate_fleet_attack(self, commands, mothership_id, attack_id):
        commands[mothership_id] = AttackCommand(attack_id)
        for fighter in self.fighters.values():
            commands[fighter.id] = MoveCommand(Destination(target=mothership_id))

    def initiate_fighters_attack(self, commands, attack_id):
        for fighter in self.fighters.values():
            commands[fighter.id] = AttackCommand(attack_id)
            fighter.attack = True

    def move_fleet_to_center(self, commands, mothership_id, pos=None):
        if pos is None:
            pos = [int(self.shippers_center[0]), int(self.shippers_center[1])]
            if pos[0] == -10000:
                return
        commands[mothership_id] = MoveCommand(Destination(coordinates=pos))
        for fighter in self.fighters.values():
            commands[fighter.id] = MoveCommand(Destination(target=mothership_id))

    def hadrian_wall(self, commands, mothership_id, mothership, fighters, enemy_ships):
        """
        Attacks intruders in given RADIUS by sending our mothership.
        When on sight (ATTACK_RADIUS), fighters surrounding our motherships are sent into battle.
        """

        intruders = find_ships_in_radius(mothership.position, RADIUS, enemy_ships)
        targets = find_ships_in_radius(mothership.position, ATTACK_RADIUS, enemy_ships)

        any_fighter_attacking = False
        for fighter_id, fighter in self.fighters.items():
            if fighters[fighter_id].command is None:
                fighter.attack = False
            any_fighter_attacking &= fighter.attack

        # we destroyed intruders, turn off the attack and return to base
        if self.threat_active and len(intruders.keys()) == 0:
            self.threat_active = False
            self.move_fleet_to_center(commands, mothership_id)
        # new threat has appeared
        if not any_fighter_attacking and len(intruders.keys()) > 0:
            self.threat_active = True
            self.initiate_fleet_attack(commands, mothership_id, next(iter(intruders)))
        # we are combatting now but higher priority enemy has appeared close
        #if self.threat_active and any_fighter_attacking and len(targets.keys()) > 0:
        #    self.redirect_attack(commands, next(iter(targets)))
        # we are close, initiate full scale attack
        if self.threat_active and not any_fighter_attacking and len(targets.keys()) > 0:
            self.initiate_fighters_attack(commands, next(iter(targets)))

    def game_logic(self):
        # todo throw all this away
        self.recreate_me()

        fighters = self._get_fighters(ship_class="4")
        shipper_count, shippers = self._get_free_fighters(ship_class="3")
        enemy_fighters = self._get_enemy_ships(ship_class="4")
        enemy_motherships = self._get_enemy_ships(ship_class="1")
        enemy_ships = self._get_enemy_ships(ship_class=None)
        mothership_id, mothership = self._get_our_mothership()
        commands = {}

        self._update_shippers_center(shippers)

        if mothership_id != "0":
            if self.build_finished:
                """
                if get_dist(
                        self.shippers_center[0], self.shippers_center[1], mothership.position[0], mothership.position[1]
                ) > TRADE_CENTER_TOL:
                    self.move_fleet_to_center(commands, mothership)
                """

                #self.move_fleet_to_center(commands, mothership_id, pos=[461, -924])
                self.hadrian_wall(commands, mothership_id, mothership, fighters, enemy_ships)
                # todo fallback if mothership is dead but fighters are not
            for i in range(3 - len(fighters.keys())):
                commands[mothership_id] = ConstructCommand(ship_class="4")
            if not self.build_finished and len(self.fighters.keys()) == 3:
                self.build_finished = True
        else:
            for ship_id, ship in shippers.items():
                commands[ship_id] = DecommissionCommand()

        # trades here

        """
        if len(enemy_duck_fighters.keys()) > 0:
            attack_id = self._get_closest_ship_to_all_fighters(enemy_duck_fighters, fighters)
        elif len(enemy_duck_motherships.keys()) > 0:
            attack_id = self._get_closest_ship_to_all_fighters(enemy_duck_motherships, fighters)
        elif len(enemy_fighters.keys()) > 0:
            attack_id = self._get_closest_ship_to_all_fighters(enemy_fighters, fighters)
        elif len(enemy_motherships.keys()) > 0:
            attack_id = self._get_closest_ship_to_all_fighters(enemy_motherships, fighters)
        elif len(enemy_ships.keys()) > 0:
            attack_id = self._get_closest_ship_to_all_fighters(enemy_ships, fighters)
        """

        pprint(commands) if commands else None
        try:
            self.client.commands_post(commands)
        except ApiException as e:
            if e.status == 400:
                print("some commands failed")
                print(e.body)

    def login(self) -> str:
        if self.config["user"] == "?":
            raise ConfigException
        if self.config["password"] == "?":
            raise ConfigException
        player, status, headers = self.client.login_post_with_http_info(Credentials(
            username=self.config["user"],
            password=self.config["password"],
        ), _return_http_data_only=False)
        self.client.api_client.cookie = headers['Set-Cookie']
        player: PlayerId = player
        return player.id


def main_loop(api_client, config):
    game_api = GameApi(api_client=api_client)
    while True:
        try:
            game = Game(game_api, config)
            game.game_loop()
            print("season ended")
        except ConfigException as e:
            print(f"User / password was not configured in the config file [{CONFIG_FILE}]")
            return
        except Exception as e:
            print(f"Unexpected error {e}")


def main():
    config = yaml.safe_load(open(CONFIG_FILE))
    print(f"Loaded config file {CONFIG_FILE}")
    print(f"Loaded config values {config}")
    configuration = Configuration()
    if config["host"] == "?":
        print(f"Host was not configured in the config file [{CONFIG_FILE}]")
        return

    configuration.host = config["host"]

    main_loop(ApiClient(configuration=configuration, cookie="SESSION_ID=1"), config)


def get_dist(x1, y1, x2, y2) -> float:
    return ((x1 - x2) ** 2 + (y1 - y2) ** 2) ** 0.5


def get_path_from_to(x1, y1, x2, y2):
    pass


def get_enemy_ships(ship_items, ship_class=None, ship_player=None) -> dict:
    ships: Dict[Ship] = {}
    for ship_id, ship in ship_items.items():
        if ship_class is not None and ship.ship_class != ship_class:
            continue
        if ship_player is not None and ship.player != ship_player:
            continue
        if ship_player is None and ship.player == "1":
            continue
        ships[ship_id] = ship

    return ships


def find_ships_in_radius(pos: Tuple[float, float], radius, enemy_ships):
    found_ships = {}
    for ship_id, ship in enemy_ships.items():
        if get_dist(pos[0], pos[1], ship.position[0], ship.position[1]) <= radius:
            found_ships[ship_id] = ship

    return found_ships


def test_ships():
    ship_items = {
        "1": Ship(ship_class="4", player="1", life=1000, name="d", position=[0, 0], prev_position=[0, 0], resources=""),
        "2": Ship(ship_class="4", player="2", life=1000, name="d", position=[0, 0], prev_position=[0, 0], resources=""),
        "3": Ship(ship_class="4", player="5", life=1000, name="d", position=[0, 0], prev_position=[0, 0], resources=""),
        "4": Ship(ship_class="4", player="5", life=1000, name="d", position=[0, 0], prev_position=[0, 0], resources=""),
        "5": Ship(ship_class="1", player="1", life=1000, name="d", position=[0, 0], prev_position=[0, 0], resources=""),
        "6": Ship(ship_class="1", player="5", life=1000, name="d", position=[0, 0], prev_position=[0, 0], resources=""),
    }
    enemy_duck_fighters = get_enemy_ships(ship_items, ship_class="4", ship_player="5")
    enemy_duck_motherships = get_enemy_ships(ship_items, ship_class="1", ship_player="5")
    enemy_motherships = get_enemy_ships(ship_items, ship_class="1")
    enemy_ships = get_enemy_ships(ship_items, ship_class=None)

    assert list(enemy_duck_fighters.keys()) == ["3", "4"]
    assert list(enemy_duck_motherships.keys()) == ["6"]
    assert list(enemy_motherships.keys()) == ["6"]
    assert list(enemy_ships.keys()) == ["2", "3", "4", "6"]


if __name__ == '__main__':
    main()
    #test_ships()
