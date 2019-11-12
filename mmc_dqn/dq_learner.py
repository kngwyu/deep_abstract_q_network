import interfaces
import tensorflow as tf
import numpy as np
import tf_helpers as th
from mmc_replay_memory import ReplayMemory, MMCPathTracker


class DQLearner(interfaces.LearningAgent):

    def __init__(self, dqn, num_actions, max_path_length=1000, beta=0.5, gamma=0.99, learning_rate=0.00025, replay_start_size=50000,
                 epsilon_start=1.0, epsilon_end=0.01, epsilon_steps=1000000,
                 update_freq=4, target_copy_freq=30000, replay_memory_size=1000000,
                 max_mmc_path_length=1000, mmc_beta=0.1,
                 frame_history=4, batch_size=32, error_clip=1, restore_network_file=None, double=True):
        self.dqn = dqn
        config = tf.ConfigProto()
        config.gpu_options.allow_growth = True
        config.allow_soft_placement = True
        self.sess = tf.Session(config=config)
        self.inp_actions = tf.placeholder(tf.float32, [None, num_actions])
        self.max_mmc_path_length = max_mmc_path_length
        self.mmc_beta = mmc_beta
        inp_shape = [None] + list(self.dqn.get_input_shape()) + [frame_history]
        inp_dtype = self.dqn.get_input_dtype()
        assert type(inp_dtype) is str
        self.inp_frames = tf.placeholder(inp_dtype, inp_shape)
        self.inp_sp_frames = tf.placeholder(inp_dtype, inp_shape)
        self.inp_terminated = tf.placeholder(tf.bool, [None])
        self.inp_reward = tf.placeholder(tf.float32, [None])
        self.inp_mmc_reward = tf.placeholder(tf.float32, [None])
        self.inp_mask = tf.placeholder(inp_dtype, [None, frame_history])
        self.inp_sp_mask = tf.placeholder(inp_dtype, [None, frame_history])
        self.gamma = gamma
        with tf.variable_scope('online'):
            mask_shape = [-1] + [1] * len(self.dqn.get_input_shape()) + [frame_history]
            mask = tf.reshape(self.inp_mask, mask_shape)
            masked_input = self.inp_frames * mask
            self.q_online = self.dqn.construct_q_network(masked_input)
        with tf.variable_scope('target'):
            mask_shape = [-1] + [1] * len(self.dqn.get_input_shape()) + [frame_history]
            sp_mask = tf.reshape(self.inp_sp_mask, mask_shape)
            masked_sp_input = self.inp_sp_frames * sp_mask
            self.q_target = self.dqn.construct_q_network(masked_sp_input)

        if double:
            with tf.variable_scope('online', reuse=True):
                self.q_online_prime = self.dqn.construct_q_network(masked_sp_input)
            self.maxQ = tf.gather_nd(self.q_target, tf.transpose(
                [tf.range(0, 32, dtype=tf.int32), tf.cast(tf.argmax(self.q_online_prime, axis=1), tf.int32)], [1, 0]))
        else:
            self.maxQ = tf.reduce_max(self.q_target, reduction_indices=1)

        self.r = self.inp_reward
        use_backup = tf.cast(tf.logical_not(self.inp_terminated), dtype=tf.float32)
        self.y = self.r + use_backup * gamma * self.maxQ
        self.delta_dqn = tf.reduce_sum(self.inp_actions * self.q_online, reduction_indices=1) - self.y
        self.delta_mmc = (self.inp_mmc_reward - self.y)
        self.delta = (1. - self.mmc_beta)*self.delta_dqn + self.mmc_beta*self.delta_mmc
        self.error = tf.where(tf.abs(self.delta) < error_clip, 0.5 * tf.square(self.delta), error_clip * tf.abs(self.delta))
        self.loss = tf.reduce_sum(self.error)
        self.g = tf.gradients(self.loss, self.q_online)
        optimizer = tf.train.RMSPropOptimizer(learning_rate=learning_rate, decay=0.95, centered=True, epsilon=0.01)
        self.train_op = optimizer.minimize(self.loss, var_list=th.get_vars('online'))
        self.copy_op = th.make_copy_op('online', 'target')
        self.saver = tf.train.Saver(var_list=th.get_vars('online'))

        self.replay_buffer = ReplayMemory(self.dqn.get_input_shape(), self.dqn.get_input_dtype(), replay_memory_size, frame_history)
        self.mmc_tracker = MMCPathTracker(self.replay_buffer, self.max_mmc_path_length, self.gamma)

        self.frame_history = frame_history
        self.replay_start_size = replay_start_size
        self.epsilon = epsilon_start
        self.epsilon_min = epsilon_end
        self.epsilon_steps = epsilon_steps
        self.epsilon_delta = (self.epsilon - self.epsilon_min) / self.epsilon_steps
        self.update_freq = update_freq
        self.target_copy_freq = target_copy_freq
        self.action_ticker = 1

        self.num_actions = num_actions
        self.batch_size = batch_size

        self.sess.run(tf.initialize_all_variables())

        if restore_network_file is not None:
            self.saver.restore(self.sess, restore_network_file)
            print('Restored network from file')
        self.sess.run(self.copy_op)

    def update_q_values(self):
        S1, A, R, MMC_R, S2, T, M1, M2 = self.replay_buffer.sample(self.batch_size)
        Aonehot = np.zeros((self.batch_size, self.num_actions), dtype=np.float32)
        Aonehot[list(range(len(A))), A] = 1

        [_, loss, q_online, maxQ, q_target, r, y, error, delta, g] = self.sess.run(
            [self.train_op, self.loss, self.q_online, self.maxQ, self.q_target, self.r, self.y, self.error, self.delta,
             self.g],
            feed_dict={self.inp_frames: S1, self.inp_actions: Aonehot,
                       self.inp_sp_frames: S2, self.inp_reward: R, self.inp_mmc_reward: MMC_R,
                       self.inp_terminated: T, self.inp_mask: M1, self.inp_sp_mask: M2})
        return loss

    def run_learning_episode(self, environment, max_episode_steps=100000):
        episode_steps = 0
        total_reward = 0
        while max_episode_steps is None or episode_steps < max_episode_steps:

            if environment.is_current_state_terminal():
                self.mmc_tracker.flush()
                break

            state = environment.get_current_state()
            if np.random.uniform(0, 1) < self.epsilon:
                action = np.random.choice(environment.get_actions_for_state(state))
            else:
                action = self.get_action(state)

            if self.replay_buffer.size() > self.replay_start_size:
                self.epsilon = max(self.epsilon_min, self.epsilon - self.epsilon_delta)

            state, action, reward, next_state, is_terminal = environment.perform_action(action)
            total_reward += reward

            self.mmc_tracker.append(state[-1], action, np.sign(reward), next_state[-1], is_terminal)
            if (self.replay_buffer.size() > self.replay_start_size) and (self.action_ticker % self.update_freq == 0):
                loss = self.update_q_values()
            if (self.action_ticker - self.replay_start_size) % self.target_copy_freq == 0:
                self.sess.run(self.copy_op)
            self.action_ticker += 1
            episode_steps += 1
        return episode_steps, total_reward

    def get_action(self, state):
        size = list(np.array(list(range(len(self.dqn.get_input_shape()))))+1)
        state_input = np.transpose(state, size + [0])

        [q_values] = self.sess.run([self.q_online],
                                   feed_dict={self.inp_frames: [state_input],
                                              self.inp_mask: np.ones((1, self.frame_history), dtype=np.float32)})
        return np.argmax(q_values[0])

    def save_network(self, file_name):
        self.saver.save(self.sess, file_name)

