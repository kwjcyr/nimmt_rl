#include <iostream>
#include <map>
#include <vector>
#include <unordered_set>
#include <random>
using namespace std;

class Nimmt {
public:
    Nimmt() {
        cout << "Nimmt constructor called" << endl;
    }
    Nimmt(const vector<int>& pokers, int userNum) {
        for (int i = 0; i < pokers.size(); ++i) {
            _pokerMap[pokers[i]].push_back(pokers[i]);
            _existPokers.push_back(pokers[i]);
        }
        _userNum = userNum;
    }
    ~Nimmt() {
        cout << "Nimmt destructor called" << endl;
    }
    vector<int> generateRandomPokers() {
        unordered_set<int> excluded(_existPokers.begin(), _existPokers.end());
        vector<int> candidates;
        candidates.reserve(100 - excluded.size());
        for (int i = 1; i <= 100; ++i) {
            if (excluded.find(i) == excluded.end()) {
                candidates.push_back(i);
            }
        }
        std::random_device rd;
        std::mt19937 gen(rd());

        std::shuffle(candidates.begin(), candidates.end(), gen);
        candidates.resize(_userNum);
        ranges::sort(candidates);
        return candidates;
    }

    void addPokers(vector<int> pokers) {
        for (int poker : pokers) {
            // init tmpMap
            map<int, pair<int, int> > tmpMap; // <max, <key, size>>
            for (map<int, vector<int>>::iterator it = _pokerMap.begin(); it != _pokerMap.end(); ++it) {
                int max = it->second[it->second.size() - 1];
                tmpMap[max] = make_pair(it->first, it->second.size());
            }

            map<int, pair<int, int>>::iterator it = tmpMap.begin();
            for (; it != tmpMap.end(); ++it) {
                if (poker < it->first) {
                    break;
                }
            }

            if (it == tmpMap.begin()) {
                // 1. 比所有牌都小，需要收牌，收size最小的
                int minSize = _userNum;
                int tmpKey = 0;
                for (map<int, pair<int, int>>::iterator it2 = tmpMap.begin(); it2 != tmpMap.end(); ++it2) {
                    if (it2->second.second <= minSize) {
                        minSize = it2->second.second;
                        tmpKey = it2->second.first;
                    }
                }
                _pokerMap.erase(tmpKey);
                _pokerMap[poker].push_back(poker);
                cout << "case1 recv pokers: " << tmpKey << endl;
            } else {
                --it;
                if (it->second.second < _userNum) {
                    // 2. 正常，放到距离它最近的位置
                    _pokerMap[it->second.first].push_back(poker);
                } else {
                    // 3. 超过了size, 需要收牌
                    _pokerMap.erase(it->second.first);
                    _pokerMap[poker].push_back(poker);
                    cout << "case3 recv pokers: " << it->second.first << endl;
                }
            }

            _existPokers.push_back(poker);

        }
    }

    void program() {
        for (int i = 1; i <= 10; ++i) {
            cout << "Round " << i << ":" <<endl;
            vector<int> pokers = generateRandomPokers();
            cout << "this round pokers: " << endl;
            for (int poker : pokers) {
                cout << poker << " ";
            }
            cout << endl;
            addPokers(pokers);
            print();
        }
    }
    void print() {
        cout << "current pokers: " << endl;
        for (auto it = _pokerMap.begin(); it != _pokerMap.end(); ++it) {
            for (auto num : it->second) {
                cout << num << " ";
            }
            cout << endl;
        }
    }
private:
    map<int, vector<int> > _pokerMap;
    vector<int> _existPokers;
    int _userNum;
};

int main() {
    cout << "Hello, Nimmt!" << endl;
    Nimmt *nimmt = new Nimmt({15, 23, 90, 41, 56}, 6);
    nimmt->print();
    nimmt->program();
    delete nimmt;
    return 0;
}