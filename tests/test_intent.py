import unittest
from datetime import date

from maibot_acg_search.intent import classify


TODAY = date(2026, 11, 6)


class IntentTests(unittest.TestCase):
    def test_galgame_recommendation_keeps_requested_filters(self):
        text = "推荐两部26年国区Steam上有简中的Galgame，VNDB评分85分以上，纯爱不要NTR和后宫，别剧透"
        intent = classify(text, today=TODAY)
        self.assertEqual((intent.medium, intent.task, intent.route), ("galgame", "recommendation", "catalogue"))
        self.assertEqual(intent.query, text)
        self.assertEqual((intent.year, intent.platform, intent.region, intent.language, intent.count),
                         (2026, "Steam", "国区", "简体中文", 2))
        self.assertEqual((intent.ranking, intent.score_source, intent.min_score), ("rating", "vndb", 85))
        self.assertEqual((intent.genres, intent.exclusions, intent.spoiler_policy),
                         (("纯爱",), ("NTR", "后宫"), "avoid"))

    def test_informal_recommendation_and_alias_comparison(self):
        self.assertEqual(classify("找几部旮旯游戏").route, "catalogue")
        intent = classify("Clannad 和 Little Busters 哪个更值得玩")
        self.assertEqual((intent.medium, intent.task, intent.titles),
                         ("galgame", "comparison", ("CLANNAD", "Little Busters!")))
        self.assertEqual(classify("罚抄").titles, ("Rewrite",))
        configured = classify("推荐不如比较《玻璃鸟》和《苍彼》", aliases=["玻璃鸟=Glass Bird"])
        self.assertEqual(configured.titles, ("Glass Bird", "苍之彼方的四重奏"))

    def test_galgame_playtime_constraints(self):
        for query, expected in (
            ("推荐个5小时内的Galgame", (None, 5)),
            ("推荐三到五小时的旮旯游戏", (3, 5)),
            ("推荐不超过90分钟的Galgame", (None, 1.5)),
            ("推荐至少3小时、不超过5小时的视觉小说", (3, 5)),
            ("推荐2.5小时以上的Galgame", (2.5, None)),
        ):
            with self.subTest(query=query):
                intent = classify(query, today=TODAY)
                self.assertEqual((intent.route, intent.min_hours, intent.max_hours), ("catalogue", *expected))

    def test_playtime_ignores_release_windows_titles_and_other_media(self):
        for query in ("推荐过去5小时内发售的Galgame", "推荐《5小时内》这部Galgame", "推荐5小时以内的普通游戏"):
            with self.subTest(query=query):
                intent = classify(query, today=TODAY)
                self.assertEqual((intent.min_hours, intent.max_hours), (None, None))

    def test_media_names_and_sequels_do_not_override_anime_cues(self):
        intent = classify("《明日方舟》动画更新到哪一集了", today=TODAY)
        self.assertEqual((intent.medium, intent.task, intent.route, intent.titles, intent.date_from),
                         ("anime", "progress", "catalogue", ("明日方舟",), ""))
        intent = classify("芙莉莲第二季第3集什么时候播", today=TODAY)
        self.assertEqual((intent.task, intent.titles), ("schedule", ("芙莉莲第二季",)))
        intent = classify("《命运石之门0》和《命运石之门》哪个好")
        self.assertEqual(intent.titles, ("命运石之门0", "命运石之门"))

    def test_anime_window_and_missing_episode_subject(self):
        intent = classify("2026年10月新番放送排期", today=TODAY)
        self.assertEqual((intent.route, intent.task, intent.titles), ("catalogue", "schedule", ()))
        self.assertEqual((intent.month, intent.date_from, intent.date_to), (10, "2026-10-01", "2027-01-01"))
        self.assertEqual(classify("更新到几集了").route, "clarify")
        self.assertEqual(classify("推荐点二次元作品").route, "clarify")

    def test_external_facts_use_the_matching_route(self):
        cases = {
            "2026年有哪些新Galgame发售": ("galgame", "release", "catalogue"),
            "Galgame《星之梦》的声优是谁": ("galgame", "credits", "catalogue"),
            "Galgame《星之梦》有汉化吗": ("galgame", "localization", "catalogue"),
            "原神配队攻略": ("acg_game", "walkthrough", "web"),
            "评价下最近的COD23": ("game", "fact", "web"),
            "帮我找一下26年手机/PC好玩的游戏": ("game", "recommendation", "web"),
        }
        for message, expected in cases.items():
            with self.subTest(message=message):
                intent = classify(message, today=TODAY)
                self.assertEqual((intent.medium, intent.task, intent.route), expected)
        intent = classify("帮我找一下26年手机/PC好玩的游戏", today=TODAY)
        self.assertEqual((intent.year, intent.platform), (2026, "PC 手机"))

    def test_casual_transform_and_search_opt_out(self):
        for message in ("你好", "你是谁", "Galgame是什么", "我刚看完芙莉莲", "帮我写一段Galgame同人",
                        "别联网，推荐个Galgame", "不用查直接推荐个Galgame"):
            with self.subTest(message=message):
                self.assertEqual(classify(message).route, "skip")
        self.assertEqual(classify("帮我查一下Galgame是什么").route, "web")


if __name__ == "__main__":
    unittest.main()
