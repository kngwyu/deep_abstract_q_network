import interfaces
from collections import deque
import numpy as np
import tensorflow as tf

from l0_learner import MultiHeadedDQLearner




class L1Action(object):
    def __init__(self, initial_state, goal_state, initial_state_vec, goal_state_vec):
        self.initial_state = initial_state
        self.goal_state = goal_state
        self.initial_state_vec = initial_state_vec
        self.goal_state_vec = goal_state_vec

    def __str__(self):
        if self.goal_state is None:
            return '%s EXPLORE' % (self.initial_state,)
        else:
            return '%s -> %s' % (self.initial_state, self.goal_state)

class MovingAverageTable(object):

    def __init__(self, moving_avg_len, num_conf, rmax):
        self.moving_avg_len = moving_avg_len
        self.num_conf = num_conf
        self.rmax = rmax

        self.sa_count = dict()
        self.transition_table = dict()
        self.reward_table = dict()
        self.terminal_table = dict()
        self.valid_transitions = dict()
        self.states = set()
        self.actions = set()

    def insert(self, s, a, sp, r, terminal):
        self.states.add(s)
        self.actions.add(a)
        key = (s, a, sp)

        if (s, a) in self.sa_count:
            self.sa_count[(s, a)] += 1
        else:
            self.sa_count[(s, a)] = 0

        if (s, a) in self.valid_transitions:
            self.valid_transitions[(s, a)].add(sp)
        else:
            self.valid_transitions[(s, a)] = {sp}

        if sp not in self.terminal_table:
            self.terminal_table[sp] = deque(maxlen=self.moving_avg_len)
        self.terminal_table[sp].append(float(terminal))

        if key not in self.transition_table:
            self.transition_table[key] = deque(maxlen=self.moving_avg_len)
            self.reward_table[key] = deque(maxlen=self.moving_avg_len)

        for sp_ in self.valid_transitions[(s, a)]:
            self.transition_table[(s, a, sp_)].append(1. if sp == sp_ else 0.)
        self.reward_table[key].append(r)

    def get_p(self, s, a, sp):
        return np.mean(self.transition_table[(s, a, sp)])

    def get_r(self, s, a, sp, evaluation=False):
        if self.sa_count[(s, a)] >= self.num_conf or evaluation:
            return np.mean(self.reward_table[(s, a, sp)])
        else:
            return self.rmax

    def get_prob_terminal(self, s):
        return np.mean(self.terminal_table[s])


class RMaxLearner(interfaces.LearningAgent):

    def __init__(self, abs_size, env, abs_vec_func, abs_func, N=1000, max_VI_iterations=100, VI_delta=0.01, gamma=0.9, rmax=10, max_num_abstract_states=10, frame_history=1):
        self.env = env
        self.abs_size = abs_size
        self.abs_func = abs_func
        # abs_vec_func produces an abstraction_vector (what goes into the embedding network) for a given state.
        self.abs_vec_func = abs_vec_func
        self.rmax = rmax
        self.transition_table = MovingAverageTable(N, 1, self.rmax)
        self.max_VI_iterations = max_VI_iterations
        self.VI_delta = VI_delta
        self.values = dict()
        self.evaluation_values = dict()
        self.gamma = gamma
        self.value_update_counter = 0
        self.value_update_freq = 10
        with tf.device('/gpu:1'):
            self.l0_learner = MultiHeadedDQLearner(abs_size, len(self.env.get_actions_for_state(None)), max_num_abstract_states, frame_history=frame_history)
        self.actions_for_state = dict()
        self.neighbors = dict()
        self.states = set()
        self.current_dqn_number = 0
        self.create_new_state(self.abs_func(self.env.get_current_state()))

    def create_new_state(self, state):
        self.states.add(state)
        self.values[state] = 0
        self.evaluation_values[state] = 0
        self.actions_for_state[state] = [L1Action(state, None, self.abs_vec_func(state), self.abs_vec_func(state))]
        self.neighbors[state] = []
        self.current_dqn_number += 1

        print('Found new state: %s' % (state,))

    def add_new_action(self, state, goal_state):
        new_action = L1Action(state, goal_state, self.abs_vec_func(state), self.abs_vec_func(goal_state))
        self.actions_for_state[state].append(new_action)
        self.neighbors[state].append(goal_state)
        self.current_dqn_number += 1

        print('Found new action: %s' % (new_action,))

    def run_vi(self, values, evaluation=False):
        new_values = dict()
        for i in range(self.max_VI_iterations):
            stop = True
            for s in self.transition_table.states:
                new_values[s] = np.max(list(self.calculate_qs(s, evaluation=evaluation).values()))
                if s in values and np.abs(new_values[s] - values[s]) > self.VI_delta:
                    stop = False
            values = new_values.copy()
            if stop:
                break
        return values

    def calculate_qs(self, s, evaluation=False):
        qs = dict()
        values = self.evaluation_values if evaluation else self.values
        if evaluation:
            values = self.evaluation_values
        else:
            values = self.values
        for a in self.actions_for_state[s]:
            val = 0

            key = (s, a)
            dqn_tuple = (a.initial_state, a.goal_state)

            dqn_eps = self.l0_learner.epsilon.get(dqn_tuple, 1.0)
            # when evaluating dont use rmax for underexplored states, for invalid transitions assign 0-value.
            # if (key in self.transition_table.valid_transitions) and (dqn_eps <= self.l0_learner.epsilon_min or evaluation):
            if key in self.transition_table.valid_transitions:
                Z = np.sum([self.transition_table.get_p(s, a, sp) for sp in self.transition_table.valid_transitions[key]])
                for sp in self.transition_table.valid_transitions[key]:
                    p = self.transition_table.get_p(s, a, sp) / Z
                    r = self.get_reward(s, a, sp, evaluation=evaluation)

                    if sp in values:
                        use_backup = (1 - self.transition_table.get_prob_terminal(sp))
                        val += p * (r + self.gamma * values[sp] * use_backup)
                    else:
                        val += p * r
            else:
                if evaluation:
                    val = 0
                else:
                    val = self.rmax
            qs[a] = val
        return qs

    def get_reward(self, s, a, sp, evaluation=False):
        return self.transition_table.get_r(s, a, sp, evaluation=evaluation)
        # if evaluation:
        #     return self.transition_table.get_r(s, a, sp, evaluation=evaluation)
        # else:
        #     prop = self.l0_learner.replay_buffer.abstract_action_proportions(self.abs_vec_func(s), self.abs_vec_func(sp))
        #     return max(0, 1./len(self.transition_table.actions) - prop)

    def run_learning_episode(self, environment):
        total_episode_steps = 0
        total_reward = 0

        while not self.env.is_current_state_terminal():

            s = self.abs_func(self.env.get_current_state())
            # need to do additional check here because it is possible to "teleport" without transitioning into a new state
            # to recreate:
            '''
            pick up the key for the first time
            jump off the ledge.
            the game will stop logging "murked" states during your fall, then a terminal state will be called,
            and you will "teleport" into a new state (1, 2) with the key, without having transitioned.
            '''
            if s not in self.states:
                self.create_new_state(s)

            a = self.get_l1_action(s)
            dqn_tuple = (a.initial_state, a.goal_state)
            assert s == a.initial_state
            print('Executing action: %s -- eps: %.6f' % (a, self.l0_learner.epsilon.get(dqn_tuple, 1.0)))
            episode_steps, R, sp = self.l0_learner.run_learning_episode(self.env, a.initial_state_vec, a.goal_state_vec, s, a.goal_state, self.abs_func, self.abs_vec_func, max_episode_steps=1000)

            # #TODO: REMOVE LATER
            # abs_state = self.env.abstraction_tree.get_abstract_state()
            # in_good_sectors = abs_state.sector in [(1, 2), (1, 1), (2, 1)]
            # if not in_good_sectors:
            #     sp = s

            total_episode_steps += episode_steps
            total_reward += R

            # check transition
            if sp != s:
                if sp not in self.states:
                    self.create_new_state(sp)

                if sp not in self.neighbors[s]:
                    self.add_new_action(s, sp)

            # add transition
            self.transition_table.insert(s, a, sp, R, environment.is_current_state_terminal())

            # perform vi for both evaluation values and regular values.
            if self.value_update_counter % self.value_update_freq == 0:
                self.values = self.run_vi(self.values.copy())
            self.value_update_counter += 1

        return total_episode_steps, total_reward

    def get_l1_action(self, state, evaluation=False):
        qs = self.calculate_qs(state, evaluation=evaluation)
        keys, values = list(zip(*list(qs.items())))
        if evaluation:
            action = np.random.choice(np.array(keys)[np.array(values) == np.max(values)])
        else:
            temp = 1.0
            norm_values = (values - np.min(values)) / (np.max(values) - np.min(values))
            distribution = np.exp(temp*norm_values) / np.sum(np.exp(temp*norm_values))
            action = keys[np.random.choice(list(range(len(distribution))), p=distribution)]
        return action

    def get_action(self, state, evaluation=False):
        l1_state = self.abs_func(state)
        l1_action = self.get_l1_action(l1_state, evaluation=evaluation)
        return self.l0_learner.get_action(state, l1_action.initial_state_vec, l1_action.goal_state_vec)

    def save_network(self, file_name):
        self.l0_learner.save_network(file_name)