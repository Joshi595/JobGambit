"""Universities' and institutes' posting lists: B-ITE listings, Umantis and coveto, lists of PDF postings."""
import json

from jobgambit.core import countries, sources
from jobgambit.countries.de import sources as de


def test_every_bite_listing_on_a_page():
    page = ('<div data-bite-jobs-api-listing="uni-kassel:atm"></div>'
            '<div data-bite-jobs-api-listing="uni-kassel:prof"></div>'
            '<div data-bite-jobs-api-listing="uni-kassel:stipendien"></div>'
            '<div data-bite-jobs-api-listing="uni-kassel:wimi"></div>')
    assert sources.bite_listings(page) == ["uni-kassel:atm", "uni-kassel:prof", "uni-kassel:stipendien", "uni-kassel:wimi"]
    assert sources.bite_listings('<script src="https://cs-assets.b-ite.com/uni-ulm/jobs-api/wissenschaftlich.min.js">') == [
        "uni-ulm:wissenschaftlich"]  # a listing's script loaded directly
    assert sources.ats_in(page, "https://uni-kassel.de/stellen") == ("bite", "uni-kassel:atm")  # ats_in names the first only


def test_a_bite_listing_searches_with_its_own_filter():
    """A customer's listings share one key: the listing's filter (research staff only) comes from its script."""
    kassel = ('var e,i;e="wbs",i=0,function(t){}("wimi").then(function(n){var l=o.createClient({key:"ab12"}),r=o.createSearchConfig({'
              'channel:i,locale:"de",sort:{by:"endsOn",order:"asc"},filter:{"custom.Zuordnung":{in:e},locale:{in:["de","en"]}}})})')
    assert sources.bite_search(kassel) == {"channel": 0, "filter": {"custom.Zuordnung": {"in": ["wbs"]}, "locale": {"in": ["de", "en"]}}}
    # a list of variables; an empty value and one the page builds are left out, as the widget does
    other = ('n=1,i=0,r="wissenschaftich_beschaeftigte",s="",c.createSearchConfig({channel:i,filter:{"custom.zuordnung":{in:[r]},'
             '"custom.homepage":{in:[s]},"custom.einheit":{in:f}}})')
    assert sources.bite_search(other) == {"channel": 0, "filter": {"custom.zuordnung": {"in": ["wissenschaftich_beschaeftigte"]}}}
    assert sources.bite_search('var l=s.createClient({key:"ab12"})') == {}


def test_a_bite_key_kept_in_a_variable(monkeypatch):
    sent = []
    script = 'var c="47d6a8",n=0,b=i.createClient({key:c}),p=i.createSearchConfig({channel:n})'
    monkeypatch.setattr(sources, "http", lambda url, data=None, headers=None, **k: sent.append(data) or (
        (200, script, url) if url.endswith(".min.js") else (200, json.dumps({"jobPostings": []}), url)))
    sources.bite("BTU", "btu:main-listing", {})
    assert sent[-1]["key"] == "47d6a8" and sent[-1]["channel"] == 0


def test_umantis_and_coveto_lists(monkeypatch):
    pages = {"https://recruitingapp-5455.de.umantis.com/Jobs/1?lang=ger":
             '<a href="/Vacancies/748/Description/1">Wissenschaftliche*r Mitarbeiter*in (w/m/d) KI</a>'
             '<a href="/Vacancies/748/Application/CheckLogin/1">Jetzt bewerben</a>',
             "https://www.zsw-bw-jobs.de/public/jobs/":
             '<tr><td><a href="https://www.zsw-bw-jobs.de/job-doktorand-m-w-d-batterie-ulm-608.html">Doktorand (m/w/d) Batterie</a></td>'
             '<td>Ulm</td><td><a href="https://www.zsw-bw-jobs.de/job-doktorand-m-w-d-batterie-ulm-608.html">Jetzt bewerben</a></td></tr>'}
    monkeypatch.setattr(sources, "http", lambda url: (200, pages[url], url))
    assert [g["title"] for g in sources.umantis("THU", "recruitingapp-5455.de.umantis.com", {})] == ["Wissenschaftliche*r Mitarbeiter*in KI"]
    assert [g["title"] for g in sources.coveto("ZSW", "www.zsw-bw-jobs.de", {})] == ["Doktorand Batterie"]  # not "Jetzt bewerben"
    assert sources.ats_ref("https://recruitingapp-5128.de.umantis.com/Vacancies/3484/Description/1") == ("umantis", "recruitingapp-5128.de.umantis.com")
    assert sources.ats_ref("https://k37591.coveto.de/public/jobs/") == ("coveto", "k37591.coveto.de")
    assert sources.ats_in('<script src="https://www.zsw-bw-jobs.de/jobs.js"></script>coveto', "https://www.zsw-bw.de/") == ("coveto", "www.zsw-bw-jobs.de")


def test_a_pack_site_the_pack_replaced_is_no_longer_scanned():
    from jobgambit.core import profile as pf
    from jobgambit.core import sites
    from jobgambit.core.data import db
    pf.write("preferences", {"targets": [{"code": "DE"}]})
    sites.add("bite", "uni-kassel:prof", "Universität Kassel", "pack")  # the professorships list an older pack had
    sites.add("bite", "uni-x:prof", "Uni X", "you")  # the person's own: theirs to keep
    sites.seed()
    with db() as con:
        got = {(r["ref"], r["origin"]) for r in con.execute("select ref, origin from sites")}
    assert ("uni-x:prof", "you") in got and ("uni-kassel:prof", "pack") not in got
    assert ("https://jobs.fraunhofer.de", "pack") in got


def test_pdf_postings_take_their_title_from_the_list_item_or_row(monkeypatch):
    body = ('<ul><li>Wissenschaftliche/r Mitarbeiter/in (m/w/d) Robotik, Kennziffer 12/26 <a href="/stellen/12-26.pdf">PDF</a></li>'
            '<li><a href="/stellen/13-26.pdf">Doktorand (m/w/d) Regelungstechnik</a></li></ul>'
            '<table><tr><td>14/26</td><td>Postdoc Quantenoptik (w/m/d)</td><td><a href="/stellen/14-26.pdf">Download (PDF, 120 KB)</a></td></tr>'
            '</table><div><a href="/stellen/alle.pdf">mehr</a></div>')
    monkeypatch.setattr(sources, "http", lambda url: (200, body, "https://uni.example/jobs"))
    got = sources.page("Uni", r"https://uni.example/jobs#re:/stellen/[^/]+\.pdf", {})
    assert [g["title"] for g in got] == ["Wissenschaftliche/r Mitarbeiter/in Robotik, Kennziffer 12/26", "Doktorand Regelungstechnik",
                                         "14/26 Postdoc Quantenoptik"]
    assert got[0]["url"] == "https://uni.example/stellen/12-26.pdf"
    assert sources.generic("Download (PDF, 120 KB)") and sources.generic("") and not sources.generic("Doktorand Robotik")


def test_every_typed_pack_site_has_a_reader_and_a_ref():
    known = {**sources.SCANNERS, **de.SCANNERS}
    for s in countries.pack("DE")["career_sites"]:
        if s.get("types"):
            assert s["kind"] in known and str(s.get("ref") or "").strip() and s.get("name"), s


def test_a_posting_linked_twice_and_a_card_with_a_heading(monkeypatch):
    body = ('<p><a href="/w/W1.pdf">Wissenschaftliche Mitarbeiterin (m/w/d)</a><br><a href="/w/W1.pdf">Institut für Informatik</a><br>'
            '<a href="/w/W1.pdf">Kennziffer W1</a></p>'
            '<p><a href="/w/W2.pdf">Mehr erfahren</a></p>'
            '<a href="/job/7"><li><span><strong>Bewerbungsfrist 30.11.2026</strong></span><h3>Doktorand (w/m/d) Robotik</h3><h4>Fachbereich 3</h4></li></a>'
            '<a href="/job/8"><h3>Postdoc Optik</h3></a><a href="/job/8">Weiterlesen</a>')
    monkeypatch.setattr(sources, "http", lambda url: (200, body, "https://uni.example/jobs"))
    got = sources.page("Uni", r"https://uni.example/jobs#re:/w/W\d\.pdf|/job/\d", {})
    assert [g["title"] for g in got] == ["Wissenschaftliche Mitarbeiterin, Institut für Informatik, Kennziffer W1",  # told apart by department
                                         "Doktorand Robotik", "Postdoc Optik"]  # the heading, not the deadline; a bare "Mehr erfahren" is no posting
    assert got[0]["key"] != sources.job_key("Uni", "Wissenschaftliche Mitarbeiterin")


def test_b_ite_older_widget_and_a_listings_company(monkeypatch):
    script = ('var n="0537263716d3f6e881eb367e960011e7ec116b0f";function l(e,i,l){}e=function(e){l(e,"#a","Ausbildung").reload(),'
              'l(e,"#b","NiWi").reload(),l(e,"#c","0","wissenschaftlich").reload()}')
    ads = {"advertisements": [{"title": "Phd Position in Statistics (m/f/d)", "job_site": "89081 Ulm", "ctime": "2026-10-21",
                               "url": {"href": "https://stellenangebote.uni-ulm.de/jobposting/bd47"}}]}
    asked = []

    def http(url, data=None, headers=None, **k):
        asked.append(url)
        return (200, script, url) if url.endswith(".min.js") else (200, json.dumps(ads), url)
    monkeypatch.setattr(sources, "http", http)
    got = sources.bite("Universität Ulm", "uni-ulm:wissenschaftlich", {})
    assert len(asked) == 2 and "custom_field1%5Bfilter%5D%5Bvalue%5D=wissenschaftlich" in asked[1]  # only the research list
    assert [(g["title"], g["company"], g["location"]) for g in got] == [("Phd Position in Statistics", "Universität Ulm", "Ulm")]
    sources.bite("Hochschule Kempten", "hs-kempten:main-listing:Verwaltung", {})
    assert "value%5D=Verwaltung" in asked[-1]  # a third part names the list to read
