# -*- coding:utf-8 -*-
from __future__ import print_function
import chainer
import chainer.functions as F
import chainer.links as L
import chainerrl
import numpy as np
import sys
import re  # 正規表現
import random
import copy
import itertools

# 定数定義 #
SIZE = 4    # ボードサイズ SIZE*SIZE
NONE = 0    # ボードのある座標にある石：なし
BLACK = 1   # ボードのある座標にある石：黒
WHITE = 2   # ボードのある座標にある石：しろ
STONE = [' ', '●', '○']    # 石の表示用
ROWLABEL = {chr(ord('a') + x): x + 1 for x in range(8)}  # ボードの横軸ラベル
N2L = [''] + [chr(ord('a') + x) for x in range(8)]
REWARD_WIN = 1      # 買った時の報酬
REWARD_LOSE = -1    # 負けた時の報酬
# ２次元のボード上での隣接８方向の定義
DIR = tuple(itertools.product(range(-1, 2), range(-1, 2)))


class QFunction(chainer.Chain):
    """ Q関数の定義 """

    def __init__(self, obs_size, n_actions, n_nodes):
        w = chainer.initializers.HeNormal(scale=1.0)    # 重みの初期化
        super(QFunction, self).__init__()
        with self.init_scope():
            self.l1 = L.Linear(obs_size, n_nodes, initialW=w)
            self.l2 = L.Linear(n_nodes, n_nodes, initialW=w)
            self.l3 = L.Linear(n_nodes, n_nodes, initialW=w)
            self.l4 = L.Linear(n_nodes, n_actions, initialW=w)

    def __call__(self, x):
        h = F.relu(self.l1(x))
        h = F.relu(self.l2(h))
        h = F.relu(self.l3(h))
        return chainerrl.action_value.DiscreteActionValue(self.l4(h))


class Board():
    """ リバーシボードクラス """
    # インスタンス（最初はボードの初期化）

    def __init__(self):
        self.board_reset()

    # ボードの初期化
    def board_reset(self):
        # 全ての石をクリア。ボードは２次元配列(i,j)で定義する
        self.board = np.zeros((SIZE, SIZE), dtype=np.float32)
        mid = SIZE // 2  # 真ん中の基準ポジション
        # 初期４つの石を配置
        self.board[mid, mid] = WHITE
        self.board[mid - 1, mid - 1] = WHITE
        self.board[mid - 1, mid] = BLACK
        self.board[mid, mid - 1] = BLACK
        self.winner = NONE  # 勝者
        self.turn = BLACK   # 黒石スタート
        self.game_end = False   # ゲーム終了チェックフラグ
        self.pss = 0    # パスチェック用フラグ。双方がパスをするとゲーム終了
        self.nofb = 0   # ボード上の黒石の数
        self.nofw = 0   # ボード上の白石の数
        self.available_pos = self.search_positions()    # self.turnの石が置ける場所のリスト

    # 石を置く＆リバース処理
    def put_stone(self, pos):
        if self.is_available(pos):
            self.board[pos[0], pos[1]] = self.turn
            self.do_reverse(pos)    # リバース
            return True
        else:
            return False

    # ターンチェンジ
    def change_turn(self):
        self.turn = WHITE if self.turn == BLACK else BLACK
        self.available_pos = self.search_positions()    # 石が置ける場所を探索しておく

    # ランダムに石を置く場所を決める　（ε-greedy用）
    def random_action(self):
        if len(self.available_pos) > 0:
            pos = random.choice(self.available_pos)  # 置く場所をランダムに決める
            pos = pos[0] * SIZE + pos[1]    # １次元座標に変換（NNの教師データは１次元でないといけない）
            return pos
        return False    # 置く場所なし

    # エージェントの行動と勝敗判定。置けない場所に置いたら負けとする
    def agent_action(self, pos):
        self.put_stone(pos)
        self.end_check()    # 石が置けたら、ゲーム終了をチェック

    # リバース処理
    def do_reverse(self, pos):
        for di, dj, in DIR:
            opp = BLACK if self.turn == WHITE else WHITE    # 対戦相手の石
            boardcopy = self.board.copy()   # 一旦ボードをコピーする（copyを使わないと参照渡しになるので注意）
            i = pos[0]
            j = pos[1]
            flag = False    # 挟み判定用フラグ
            while 0 <= i < SIZE and 0 <= j < SIZE:  # (i,j)座標が盤面内に収まっている間繰り返す
                i += di  # i座標（縦）をずらす
                j += dj  # j座標（横）をずらす
                # 盤面に収まっており、かつ相手の石だったら
                if 0 <= i < SIZE and 0 <= j < SIZE and boardcopy[i, j] == opp:
                    flag = True
                    boardcopy[i, j] = self.turn  # 自分の石にひっくり返す
                elif not(0 <= i < SIZE and 0 <= j < SIZE) or (flag == False and boardcopy[i, j] != opp):
                    break
                # 自分と同じ色の石が来れば挟んでいるのでリバース処理を確定
                elif boardcopy[i, j] == self.turn and flag == True:
                    self.board = boardcopy.copy()
                    break

    # 石が置ける場所をリストアップする。石が置ける場所がなければ「パス」となる
    def search_positions(self):
        pos = []
        emp = np.where(self.board == 0)  # 石が置かれていない場所を取得
        for i in range(emp[0].size):    # 石が置かれていない全ての座標に対して
            p = (emp[0][i], emp[1][i])  # (i,j)座標に変換
            if self.is_available(p):
                pos.append(p)   # 石が置ける場所の座標リストの生成
        return pos

    # 石が置けるかをチェックする
    def is_available(self, pos):
        if self.board[pos[0], pos[1]] != NONE:  # すでに石が置いてあれば、置けない
            return False
        opp = BLACK if self.turn == WHITE else WHITE
        for di, dj in DIR:  # ８方向の挟み（リバースできるか）チェック
            i = pos[0]
            j = pos[1]
            flag = False    # 挟み判定用フラグ
            while 0 <= i < SIZE and 0 <= j < SIZE:  # (i,j)座標が盤面内に収まっている間繰り返す
                i += di  # i座標（縦）をずらす
                j += dj  # j座標（横）をずらす
                # 盤面に収まっており、かつ相手の石だったら
                if 0 <= i < SIZE and 0 <= j < SIZE and self.board[i, j] == opp:
                    flag = True
                elif not(0 <= i < SIZE and 0 <= j < SIZE) or (flag == False and self.board[i, j] != opp) or self.board[i, j] == None:
                    break
                elif self.board[i, j] == self.turn and flag == True:  # 自分と同じ色の石
                    return True
        return False

    # ゲーム終了チェック
    def end_check(self):
        # ボードに全て石が埋まるか、双方がパスしたら
        if np.count_nonzero(self.board) == SIZE * SIZE or self.pss == 2:
            self.game_end = True
            self.nofb = len(np.where(self.board == BLACK)[0])
            self.nofw = len(np.where(self.board == WHITE)[0])
            self.winner = BLACK if len(np.where(self.board == BLACK)[0]) > len(
                np.where(self.board == WHITE)[0]) else WHITE

    # ボード表示
    def show_board(self):
        print('  ', end='')
        for i in range(1, SIZE + 1):
            print(' {}'.format(N2L[i]), end='')  # 横軸ラベル表示
        print('')
        for i in range(0, SIZE):
            print('{0:2d} '.format(i+1), end='')
            for j in range(0, SIZE):
                print('{} '.format(STONE[int(self.board[i][j])]), end='')
            print('')

# キーボードから入力した座標を２次元配列に対応するよう変換する


def convert_coordinate(pos):
    pos = pos.split(' ')
    i = int(pos[0]) - 1
    j = int(ROWLABEL[pos[1]]) - 1
    return (i, j)   # タプルで返す。iが縦、jが横


def judge(board, a, you):
    if board.winner == a:
        print('Game over. You lose!')
    elif board.winner == you:
        print('Game over You win!')
    else:
        print('Game over. Draw.')


def main():
    """ メイン関数(学習用) """
    board = Board()  # ボード初期化

    obs_size = SIZE * SIZE  # ボードサイズ（=NN入力次元数）
    n_actions = SIZE * SIZE  # 行動数はSIZE*SIZE(ボードのどこに石を置くか)
    n_nodes = 256   # 中間層のノード数
    q_func = QFunction(obs_size, n_actions, n_nodes)

    # optimizerの設定
    optimizer = chainer.optimizers.Adam(eps=1e-2)
    optimizer.setup(q_func)
    # 減衰率
    gamma = 0.99
    # ε-greedy法
    explorer = chainerrl.explorers.LinearDecayEpsilonGreedy(
        start_epsilon=1.0, end_epsilon=0.1, decay_steps=50000, random_action_func=board.random_action)
    # Experience Replay用のバッファ（十分大きく、エージェントごとに用意）
    replay_buffer_b = chainerrl.replay_buffers.ReplayBuffer(capacity=10 ** 6)
    replay_buffer_w = chainerrl.replay_buffers.ReplayBuffer(capacity=10 ** 6)
    # エージェント。黒石用・白石用のエージェントを別々に学習する。DQNを利用。バッチサイズを少し大きめに設定
    agent_black = chainerrl.agents.DQN(q_func, optimizer, replay_buffer_b, gamma, explorer,
                                       replay_start_size=1000, minibatch_size=128, update_interval=1, target_update_interval=1000)
    agent_white = chainerrl.agents.DQN(q_func, optimizer, replay_buffer_b, gamma, explorer,
                                       replay_start_size=1000, minibatch_size=128, update_interval=1, target_update_interval=1000)
    agents = ['', agent_black, agent_white]

    n_episodes = 20000  # 学習ゲーム回数
    win = 0     # 黒の勝利回数
    lose = 0    # 黒の敗北回数
    draw = 0    # 引き分け回数

    # ゲーム開始（エピソードの繰り返し実行）
    for i in range(1, n_episodes + 1):
        board.board_reset()
        rewards = [0, 0, 0]  # 報酬リセット

        while not board.game_end:   # ゲームが終わるまで繰り返す
            # print('DEBUG: rewards {}'.format(rewards))
            # 石が置けない場合はパス
            if not board.available_pos:
                board.pss += 1
                board.end_check()
            else:
                # 石を配置する場所を取得。ボードは２次元だが、NNへの入力のため１次元に変換
                boardcopy = np.reshape(board.board.copy(), (-1,))
                while True:  # 置ける場所が見つかるまで繰り返す。
                    pos = agents[board.turn].act_and_train(
                        boardcopy, rewards[board.turn])
                    pos = divmod(pos, SIZE)  # 座標を２次元(i,j)に変換
                    if board.is_available(pos):
                        break
                    else:
                        rewards[board.turn] = REWARD_LOSE   # 石が置けない場所であれば負の報酬
                # 石を配置
                board.agent_action(pos)
                if board.pss == 1:  # 石が配置できた場合にはパスフラグをリセットしておく（双方が連続パスするとゲーム終了する）
                    board.pss = 0

            # ゲーム時の処理
            if board.game_end:
                if board.winner == BLACK:
                    rewards[BLACK] = REWARD_WIN     # 黒の価値報酬
                    rewards[WHITE] = REWARD_LOSE    # 白の負け報酬
                    win += 1
                elif board.winner == 0:
                    draw += 1
                else:
                    rewards[BLACK] = REWARD_LOSE    # 黒の価値報酬
                    rewards[WHITE] = REWARD_WIN     # 白の負け報酬
                    lose += 1
                # エピソードを終了して学習
                boardcopy = np.reshape(board.board.copy(), (-1,))
                # 勝者のエージェントの学習
                agents[board.turn].stop_episode_and_train(
                    boardcopy, rewards[board.turn], True)
                board.change_turn()
                # 敗者のエージェントの学習
                agents[board.turn].stop_episode_and_train(
                    boardcopy, rewards[board.turn], True)
            else:
                board.change_turn()

        # 学習の進捗表示（100エピソードごと）
        if i % 100 == 0:
            print('===== Episode {} : black win {}, black lose {}, draw {} ====='.format(
                i, win, lose, draw))   # 勝敗数は黒石基準
            print('<BLACK> statistics: {}, epsilon {}'.format(
                agent_black.get_statistics(), agent_black.explorer.epsilon))
            print('<WHITE> statistics: {}, epsilon {}'.format(
                agent_white.get_statistics(), agent_white.explorer.epsilon))
            # カウンタ変数の初期化
            win = 0
            lose = 0
            draw = 0

        if i % 1000 == 0:   # 1000エピソードごとにモデルを保存する
            agent_black.save('agent_black_' + str(i))
            agent_white.save('agent_white_' + str(i))


def main_play():
    """ メイン関数(プレイ用) """
    board = Board()  # ボード初期化

    obs_size = SIZE * SIZE  # ボードサイズ（=NN入力次元数）
    n_actions = SIZE * SIZE  # 行動数はSIZE*SIZE(ボードのどこに石を置くか)
    n_nodes = 256   # 中間層のノード数
    q_func = QFunction(obs_size, n_actions, n_nodes)

    # optimizerの設定
    optimizer = chainer.optimizers.Adam(eps=1e-2)
    optimizer.setup(q_func)
    # 減衰率
    gamma = 0.99
    # ε-greedy法
    explorer = chainerrl.explorers.LinearDecayEpsilonGreedy(
        start_epsilon=1.0, end_epsilon=0.1, decay_steps=50000, random_action_func=board.random_action)
    # Experience Replay用のバッファ（十分大きく、エージェントごとに用意）
    replay_buffer = chainerrl.replay_buffers.ReplayBuffer(capacity=10 ** 6)
    # エージェント。DQNを利用。バッチサイズを少し大きめに設定
    agent = chainerrl.agents.DQN(q_func, optimizer, replay_buffer, gamma, explorer,
                                 replay_start_size=1000, minibatch_size=128, update_interval=1, target_update_interval=1000)

    ### ここからゲームスタート ###
    print('=== リバーシ ===')
    you = input('先行（黒石, 1） or 後攻（白石, 2）を選択：')
    you = int(you)
    trn = you
    assert(you == BLACK or you == WHITE)
    level = input('難易度（弱 1〜10 強）')
    level = int(level) * 2000
    if you == BLACK:
        s = '「●」（先行）'
        file = 'agent_white_' + str(level)
        a = WHITE
    else:
        s = '「◯」（後攻）'
        file = 'agent_black_' + str(level)
        a = BLACK
    agent.load(file)
    print('あなたは{}です。ゲームスタート！'.format(s))
    board.show_board()

    # ゲーム開始
    while not board.game_end:
        if trn == 2:
            boardcopy = np.reshape(board.board.copy(), (-1,))  # ボードを１次元に変換
            pos = divmod(agent.act(boardcopy), SIZE)
            # NNで置く場所が置けない場所であれば置ける場所からランダムに選択する
            if not board.is_available(pos):
                pos = board.random_action()
                if not pos:  # 置く場所がなければパス
                    board.pss += 1
                else:
                    pos = divmod(pos, SIZE)  # 座標を２次元に変換
            print('エージェントのターン --> ', end='')
            if board.pss > 0 and not pos:
                print('パスします。{}'.format(board.pss))
            else:
                board.agent_action(pos)  # posに石を置く
                board.pss = 0
                print('({},{})'.format(pos[0]+1, N2L[pos[1]+1]))
            board.show_board()
            board.end_check()
            if board.game_end:
                judge(board, a, you)
                continue
            board.change_turn()  # エージェント　--> You

        while True:
            print('あなたのターン。')
            if not board.search_positions():
                print('パスします')
                board.pss += 1
            else:
                pos = input('どこに石を置きますか？（行列で指定。例 "4 c"）：')
                if not re.match(r'[0-9] [a-z]', pos):
                    print('正しく座標を入力してください。')
                    continue
                else:
                    if not board.is_available(convert_coordinate(pos)):  # 置けない場所に置いた場合
                        print('ここには石を置けません。')
                        continue
                    board.agent_action(convert_coordinate(pos))
                    board.show_board()
                    board.pss = 0
            break
        board.end_check()
        if board.game_end:
            judge(board, a, you)
            continue

        trn = 2
        board.change_turn()


if __name__ == '__main__':
    # main()
    main_play()
