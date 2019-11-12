import datetime
import os

import numpy as np
import tqdm

import atari
import coin_game
import daqn
import tabular_coin_game
import tabular_dqn
import wind_tunnel
from vae.vae_dqn import dq_learner, atari_dqn

# import daqn_clustering
# import dq_learner_priors

num_steps = 50000000
test_interval = 250000
test_frames = 125000
game_dir = './roms'

vis_update_interval = 10000


def evaluate_agent_reward(steps, env, agent, epsilon):
    env.terminate_on_end_life = False
    env.reset_environment()
    total_reward = 0
    episode_rewards = []
    for i in tqdm.tqdm(list(range(steps))):
        if env.is_current_state_terminal():
            episode_rewards.append(total_reward)
            total_reward = 0
            env.reset_environment()
        state = env.get_current_state()
        if np.random.uniform(0, 1) < epsilon:
            action = np.random.choice(env.get_actions_for_state(state))
        else:
            action = agent.get_action(state)

        state, action, reward, next_state, is_terminal = env.perform_action(action)
        total_reward += reward
    if not episode_rewards:
        episode_rewards.append(total_reward)
    return episode_rewards


def train(agent, env, test_epsilon, results_dir):
    # open results file
    results_fn = '%s/%s_results.txt' % (results_dir, game)
    if not os.path.isdir(results_dir):
        os.mkdir(results_dir)
    results_file = open(results_fn, 'w')

    step_num = 0
    steps_until_test = test_interval
    steps_until_vis_update = 0
    best_eval_reward = - float('inf')
    while step_num < num_steps:
        env.reset_environment()
        env.terminate_on_end_life = True
        start_time = datetime.datetime.now()
        episode_steps, episode_reward = agent.run_learning_episode(env)
        end_time = datetime.datetime.now()
        step_num += episode_steps

        print('Steps:', step_num, '\tEpisode Reward:', episode_reward, '\tSteps/sec:', episode_steps / (
        end_time - start_time).total_seconds(), '\tL1Eps:', agent.epsilon)#, '\tL0Eps:', agent.l0_learner.epsilon

        # print 'Steps:', step_num, '\tEpisode Reward:', episode_reward, '\tSteps/sec:', episode_steps / (
        #     end_time - start_time).total_seconds(), '\tEps:', agent.epsilon

        steps_until_test -= episode_steps
        if steps_until_test <= 0:
            steps_until_test += test_interval
            print('Evaluating network...')
            episode_rewards = evaluate_agent_reward(test_frames, env, agent, test_epsilon)
            mean_reward = np.mean(episode_rewards)

            if mean_reward > best_eval_reward:
                best_eval_reward = mean_reward
                agent.save_network('%s/%s_best_net.ckpt' % (results_dir, game))

            print('Mean Reward:', mean_reward, 'Best:', best_eval_reward)
            results_file.write('Step: %d -- Mean reward: %.2f\n' % (step_num, mean_reward))
            results_file.flush()

        steps_until_vis_update -= episode_steps
        if steps_until_vis_update <= 0:
            steps_until_vis_update += vis_update_interval
            env.visualize_l1_states(agent.sigma_query_probs, agent.inp_frames, agent.inp_mask, agent.sess)

def train_dqn(env, num_actions):
    results_dir = './results/dqn/coin_game'

    training_epsilon = 0.1
    test_epsilon = 0.05

    frame_history = 1
    dqn = atari_dqn.AtariDQN(frame_history, num_actions, shared_bias=False)
    agent = dq_learner.DQLearner(dqn, num_actions, target_copy_freq=10000, epsilon_end=training_epsilon, double=False, frame_history=frame_history)
    train(agent, env, test_epsilon, results_dir)

def train_tabular_dqn(env, num_actions):
    results_dir = './results/dqn/tab_coin_game_lr0.0025_rp10000'
    training_epsilon = 0.1
    test_epsilon = 0.05
    n = 3
    frame_history = 1
    dqn = tabular_dqn.TabularDQN(n, frame_history, num_actions, shared_bias=False)
    agent = dq_learner.DQLearner(dqn, num_actions, target_copy_freq=3000, epsilon_end=training_epsilon,
                                 double=False, frame_history=frame_history, learning_rate=0.0025,
                                 replay_start_size=10000, epsilon_steps=100000., replay_memory_size=10001
                                 )
    train(agent, env, test_epsilon, results_dir)


def train_double_dqn(env, num_actions):
    results_dir = './results/double_dqn/wind_tunnel'

    training_epsilon = 0.01
    test_epsilon = 0.001

    frame_history = 1
    dqn = atari_dqn.AtariDQN(frame_history, num_actions)
    agent = dq_learner.DQLearner(dqn, num_actions, frame_history=frame_history, epsilon_end=training_epsilon)

    train(agent, env, test_epsilon, results_dir)

def train_daqn(env, num_actions):
    results_dir = './results/daqn/coin_game_with_base_dqn_diff_vis_trained_reward_fixed'
    env.results_dir = results_dir

    training_epsilon = 0.1
    test_epsilon = 0.05

    # agent = daqn.L1_Learner(2, num_actions, abstraction_function=env.abstraction, epsilon_end=training_epsilon)
    agent = daqn.L1_Learner(2, num_actions, learning_rate=0.00001, epsilon_end=training_epsilon, base_network_file='./base_net.ckpt')
    agent.l0_learner.epsilon = 0.1

    train(agent, env, test_epsilon, results_dir)
#
# def train_daqn_priors(env, num_actions):
#     results_dir = './results/daqn_priors/coin_game'
#     env.results_dir = results_dir
#
#     training_epsilon = 0.1
#     test_epsilon = 0.05
#
#     agent = daqn_clustering.L1_Learner(2, num_actions, epsilon_end=training_epsilon)
#
#     train(agent, env, test_epsilon, results_dir)
#
# def train_dqn_priors(env, num_actions):
#     results_dir = './results/priors/coin_game'
#     env.results_dir = results_dir
#
#     training_epsilon = 0.1
#     test_epsilon = 0.05
#
#     frame_history = 1
#     dqn = dq_learner_priors.AtariDQN(frame_history, num_actions, shared_bias=False)
#     agent = dq_learner_priors.DQLearner(dqn, num_actions, target_copy_freq=10000, epsilon_end=training_epsilon,
#                                  frame_history=frame_history, restore_network_file='results/dqn/coin_game/coin_game_best_net.ckpt',
#                                  epsilon_start=training_epsilon, replay_start_size=1000)
#
#     train(agent, env, test_epsilon, results_dir)

def setup_atari_env():
    # create Atari environment
    env = atari.AtariEnvironment(game_dir + '/' + game + '.bin')
    num_actions = len(env.ale.getMinimalActionSet())
    return env, num_actions

def setup_coin_env():
    env = coin_game.CoinGame()
    num_actions = 4
    return env, num_actions

def setup_wind_tunnel_env():
    env = wind_tunnel.WindTunnel()
    num_actions = len(env.get_actions_for_state(None))
    return env, num_actions

def setup_tabular_env():
    env = tabular_coin_game.TabularCoinGame()
    num_actions = len(env.get_actions_for_state(None))
    return env, num_actions


game = 'coin_game'
# train_dqn(*setup_coin_env())
# train_double_dqn(*setup_coin_env())
train_daqn(*setup_coin_env())
# train_daqn_priors(*setup_coin_env())
# train_dqn_priors(*setup_coin_env())
#train_tabular_dqn(*setup_tabular_env())
# game = 'wind_tunnel'
# train_double_dqn(*setup_wind_tunnel_env())