# HireSignal Source Registry

Seed input for `scripts/seed.py`. Each `##` section becomes a category of sources; each
table row becomes one company plus one source row per non-`N/A` URL column.

> **Provenance:** generated via Gemini deep research. URLs and ATS assignments are
> **unverified** and some are known to be wrong (e.g. `github.com/zepto` is the Zepto.js
> JavaScript library, not the Indian quick-commerce company). The seed script validates
> every URL at seed time and marks anything that fails as `status='pending'` for manual
> review rather than `status='approved'`.

Columns are identical across every table:

`Name | Category | Website | Country | Career Page | Company Blog | Engineering Blog | GitHub Organization | RSS Feed | ATS Used | Public API Available | API Documentation | Crawl Method Recommendation | Suggested Crawl Frequency | Priority | Notes`

---

## ats_patterns

ATS vendors themselves. These are **integration patterns**, not companies to track for
hiring momentum — the seed script skips them as companies but uses the `Notes` column to
derive ATS endpoint templates.

| Name | Category | Website | Country | Career Page | Company Blog | Engineering Blog | GitHub Organization | RSS Feed | ATS Used | Public API Available | API Documentation | Crawl Method Recommendation | Suggested Crawl Frequency | Priority | Notes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Greenhouse | ATS Provider | https://www.greenhouse.io | USA | N/A | https://www.greenhouse.com/blog | https://tech.greenhouse.io | https://github.com/greenhouse | https://www.greenhouse.com/blog/feed.xml | Greenhouse | Yes | https://developers.greenhouse.io/job-board.html | API | Hourly | High | GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true requires no authentication. |
| Lever | ATS Provider | https://www.lever.co | USA | N/A | https://www.lever.co/blog | N/A | https://github.com/lever | https://www.lever.co/blog/feed/ | Lever | Yes | https://hire.help.lever.co/hc/en-us/articles/360000000000-Lever-s-Postings-API | API | Hourly | High | GET https://api.lever.co/v0/postings/{companyToken}?mode=json provides structured commitment and team categories. |
| Ashby | ATS Provider | https://www.ashbyhq.com | USA | N/A | https://www.ashbyhq.com/blog | N/A | https://github.com/ashbyhq | https://www.ashbyhq.com/blog/feed.xml | Ashby | Yes | https://developers.ashbyhq.com/reference/job-board-api | API | Hourly | High | GET https://api.ashbyhq.com/posting-api/job-board/{companyToken}?includeCompensation=true parses multi-currency salary bands. |
| Workday | ATS Provider | https://www.workday.com | USA | N/A | https://blog.workday.com | N/A | https://github.com/workday | https://blog.workday.com/feed | Workday | Yes | N/A | Playwright | Daily | High | POST https://{tenant}.wd3.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs requires payload with limits and offsets. |
| Workable | ATS Provider | https://www.workable.com | UK | N/A | https://www.workable.com/stories | N/A | https://github.com/workable | https://www.workable.com/stories/feed | Workable | Yes | https://resources.workable.com/api | API | Daily | High | GET https://apply.workable.com/api/v1/companies/{companyToken}/jobs exposes structured multi-location data. |
| BambooHR | ATS Provider | https://www.bamboohr.com | USA | N/A | https://www.bamboohr.com/blog | N/A | https://github.com/bamboohr | https://www.bamboohr.com/blog/feed/ | BambooHR | Yes | https://documentation.bamboohr.com/docs/getting-started | API | Daily | High | GET https://{companyToken}.bamboohr.com/jobs/embed2.php?type=json surfaces an XML/JSON hybrid feed. |
| Breezy HR | ATS Provider | https://breezy.hr | USA | N/A | https://breezy.hr/blog | N/A | https://github.com/breezyhr | https://breezy.hr/blog/feed | Breezy HR | Yes | https://developer.breezy.hr/reference/webhook-security | API | Daily | High | GET https://{companyToken}.breezy.hr/positions?format=json provides a straightforward array return. |
| Recruitee | ATS Provider | https://recruitee.com | Netherlands | N/A | https://recruitee.com/blog | N/A | https://github.com/recruitee | https://recruitee.com/blog/feed | Recruitee | Yes | https://api.recruitee.com/docs/ | API | Daily | High | GET https://{companyToken}.recruitee.com/api/offers/ exposes unauthenticated JSON payloads. |
| SmartRecruiters | ATS Provider | https://www.smartrecruiters.com | USA | N/A | https://www.smartrecruiters.com/blog | N/A | https://github.com/smartrecruiters | https://www.smartrecruiters.com/blog/feed | SmartRecruiters | Yes | https://developers.smartrecruiters.com/docs/careers-page-api | API | Daily | High | GET https://api.smartrecruiters.com/v1/companies/{companyToken}/postings requires per-tenant slug discovery. |
| Pinpoint | ATS Provider | https://www.pinpointhq.com | UK | N/A | https://www.pinpointhq.com/blog | N/A | https://github.com/pinpoint-hq | https://www.pinpointhq.com/blog/feed | Pinpoint | Yes | https://api.pinpointhq.com/docs | API | Daily | High | GET https://{companyToken}.pinpointhq.com/jobs.json yields clean JSON structural metadata. |
| Freshteam | ATS Provider | https://www.freshworks.com/freshteam/ | India | N/A | https://www.freshworks.com/freshteam/blog/ | N/A | https://github.com/freshworks | https://www.freshworks.com/freshteam/blog/feed/ | Freshteam | Yes | https://developers.freshteam.com/ | API | Daily | High | GET https://{company}.freshteam.com/jobs.json delivers normalized payloads frequently used by Indian scaleups. |
| Keka Hire | ATS Provider | https://www.keka.com/recruitment-software | India | N/A | https://www.keka.com/blog | N/A | N/A | https://www.keka.com/blog/feed | Keka Hire | Yes | https://developers.keka.com | API | Daily | High | GET https://{company}.keka.com/api/v1/hire/jobs powers embedded career page rendering. |
| Zoho Recruit | ATS Provider | https://www.zoho.com/recruit/ | India | N/A | https://www.zoho.com/recruit/blog/ | N/A | https://github.com/zoho | https://www.zoho.com/recruit/blog/feed/ | Zoho Recruit | Yes | https://www.zoho.com/recruit/developer-guide/apiv2/ | API | Daily | High | GET https://recruit.zoho.com/recruit/v2/public/Job_Openings?digest={digest_key} requires digest token routing. |
| Darwinbox | ATS Provider | https://darwinbox.com | India | N/A | https://darwinbox.com/blog | N/A | N/A | https://darwinbox.com/blog/feed | Darwinbox | No | N/A | Playwright | Daily | Medium | Requires complex DOM traversal via headless browsers as a universal public JSON endpoint is restricted. |

---

## indian_unicorns

| Name | Category | Website | Country | Career Page | Company Blog | Engineering Blog | GitHub Organization | RSS Feed | ATS Used | Public API Available | API Documentation | Crawl Method Recommendation | Suggested Crawl Frequency | Priority | Notes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Zepto | Indian Unicorn | https://www.zepto.com | India | https://careers.zepto.com | https://blog.zepto.com | https://engineering.zepto.com | https://github.com/zepto | https://engineering.zepto.com/feed | Lever | Yes | https://hire.help.lever.co/ | API | Daily | High | Valued at $5.9B; scaling rapid commerce logistics engineering heavily. |
| Zerodha | Indian Unicorn | https://zerodha.com | India | https://careers.zerodha.com | https://zerodha.com/z-connect/ | https://zerodha.tech | https://github.com/zerodha | https://zerodha.tech/index.xml | Custom | No | N/A | Playwright | Daily | High | Highly profitable fintech unicorn valued at $8.2B; requires DOM extraction. |
| Razorpay | Indian Unicorn | https://razorpay.com | India | https://razorpay.com/jobs/ | https://razorpay.com/blog/ | https://engineering.razorpay.com/ | https://github.com/razorpay | https://engineering.razorpay.com/feed | Greenhouse | Yes | https://developers.greenhouse.io/job-board.html | API | Daily | High | Critical B2B payment infrastructure; massive open-source footprint. |
| Lenskart | Indian Unicorn | https://www.lenskart.com | India | https://hiring.lenskart.com | https://blog.lenskart.com | https://tech.lenskart.com | https://github.com/lenskart | https://tech.lenskart.com/feed | Greenhouse | Yes | https://developers.greenhouse.io/job-board.html | API | Daily | High | Omnichannel expansion driving simultaneous hardware and software hiring. |
| Groww | Indian Unicorn | https://groww.in | India | https://groww.in/about-us/careers | https://groww.in/blog | https://tech.groww.in | https://github.com/groww | N/A | Greenhouse | Yes | https://developers.greenhouse.io/job-board.html | API | Daily | High | Disrupting retail wealth management; intensive data engineering requirements. |
| Postman | Indian Unicorn | https://www.postman.com | India | https://www.postman.com/company/careers/ | https://blog.postman.com/ | https://medium.com/better-practices | https://github.com/postmanlabs | https://blog.postman.com/feed/ | Ashby | Yes | https://developers.ashbyhq.com/reference/job-board-api | API | Daily | High | Premier developer tool; Ashby API provides highly specific engineering compensation tiers. |
| Freshworks | Indian Unicorn | https://www.freshworks.com | India | https://www.freshworks.com/company/careers/ | https://www.freshworks.com/blog/ | https://medium.com/freshworks-engineering | https://github.com/freshworks | N/A | Freshteam | Yes | https://developers.freshteam.com/ | API | Daily | High | Publicly listed; utilizes internal proprietary HRMS for job distribution. |
| Dream11 | Indian Unicorn | https://www.dream11.com | India | https://about.dream11.com/careers | N/A | https://tech.dream11.com | https://github.com/dream11 | https://tech.dream11.com/feed | Greenhouse | Yes | https://developers.greenhouse.io/job-board.html | API | Daily | High | Experiences intense seasonal hiring spikes surrounding major sporting tournaments. |
| Swiggy | Indian Unicorn | https://www.swiggy.com | India | https://careers.swiggy.com | https://blog.swiggy.com | https://bytes.swiggy.com | https://github.com/swiggy-private | https://bytes.swiggy.com/feed | Workday | Yes | N/A | Playwright | Daily | High | Workday implementation necessitates targeted POST requests for job enumeration. |
| Flipkart | Indian Unicorn | https://www.flipkart.com | India | https://www.flipkartcareers.com | https://stories.flipkart.com | https://tech.flipkart.com | https://github.com/Flipkart | https://tech.flipkart.com/feed | Workday | Yes | N/A | Playwright | Daily | High | Maintained distinct technical recruitment operations post-Walmart acquisition. |
| Nykaa | Indian Unicorn | https://www.nykaa.com | India | https://careers.nykaa.com | https://www.nykaa.com/beauty-blog/ | N/A | N/A | N/A | Greenhouse | Yes | https://developers.greenhouse.io/job-board.html | API | Daily | High | E-commerce scaling requires localized supply chain technology roles. |
| Cred | Indian Unicorn | https://cred.club | India | https://careers.cred.club | https://cred.club/blog | N/A | https://github.com/CRED-CLUB | N/A | Greenhouse | Yes | https://developers.greenhouse.io/job-board.html | API | Daily | High | Establishes top-decile compensation benchmarks for mobile and backend engineers. |
| Meesho | Indian Unicorn | https://www.meesho.com | India | https://careers.meesho.com | https://meesho.io/blog | https://meesho.io/tech | https://github.com/meesho-tech | https://meesho.io/tech/rss | Greenhouse | Yes | https://developers.greenhouse.io/job-board.html | API | Daily | High | Tier-2/3 city e-commerce infrastructure demands scalable distributed systems. |
| BrowserStack | Indian Unicorn | https://www.browserstack.com | India | https://www.browserstack.com/careers | https://www.browserstack.com/blog | https://www.browserstack.com/engineering | https://github.com/browserstack | https://www.browserstack.com/engineering/rss | Greenhouse | Yes | https://developers.greenhouse.io/job-board.html | API | Daily | High | Device infrastructure and SaaS testing layer scaling globally. |
| Chargebee | Indian Unicorn | https://www.chargebee.com | India | https://careers.chargebee.com | https://www.chargebee.com/blog/ | N/A | https://github.com/chargebee | https://www.chargebee.com/blog/feed/ | Lever | Yes | https://hire.help.lever.co/ | API | Daily | High | Subscription billing software necessitates intensive cybersecurity recruitment. |
| Darwinbox | Indian Unicorn | https://darwinbox.com | India | https://darwinbox.com/careers | https://darwinbox.com/blog | N/A | N/A | https://darwinbox.com/blog/feed | Darwinbox | No | N/A | Playwright | Daily | High | The company utilizes its own HRMS platform for internal recruitment. |
| PhysicsWallah | Indian Unicorn | https://www.pw.live | India | https://www.pw.live/careers | N/A | N/A | N/A | N/A | Keka Hire | Yes | https://developers.keka.com/docs | API | Daily | High | Highly profitable edtech driving mass volume hiring through the Keka framework. |
| Innovaccer | Indian Unicorn | https://innovaccer.com | India | https://innovaccer.com/careers | https://innovaccer.com/blogs | N/A | https://github.com/innovaccer | N/A | Greenhouse | Yes | https://developers.greenhouse.io/job-board.html | API | Daily | High | AI-driven healthcare data aggregation heavily reliant on data science talent. |
| Acko | Indian Unicorn | https://www.acko.com | India | https://www.acko.com/careers/ | https://www.acko.com/blog/ | https://tech.acko.com | https://github.com/acko | https://tech.acko.com/feed | Greenhouse | Yes | https://developers.greenhouse.io/job-board.html | API | Daily | High | Disrupting legacy insurance architectures through digital-first data models. |
| OneCard | Indian Unicorn | https://getonecard.app | India | https://getonecard.app/careers/ | N/A | N/A | N/A | N/A | Greenhouse | Yes | https://developers.greenhouse.io/job-board.html | API | Daily | High | Credit infrastructure scaling demands rapid mobile application development teams. |

---

## indian_startups

| Name | Category | Website | Country | Career Page | Company Blog | Engineering Blog | GitHub Organization | RSS Feed | ATS Used | Public API Available | API Documentation | Crawl Method Recommendation | Suggested Crawl Frequency | Priority | Notes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| UnifyApps | Indian DevTools | https://www.unifyapps.com | India | https://www.unifyapps.com/careers | https://www.unifyapps.com/blog | N/A | N/A | N/A | Ashby | Yes | https://developers.ashbyhq.com/reference/job-board-api | API | Daily | High | Raised $50M led by WestBridge Capital; aggressive hiring across enterprise integration layers. |
| Coram AI | Indian Deeptech | https://www.coram.ai | India | https://www.coram.ai/careers | https://www.coram.ai/blog | N/A | N/A | N/A | Lever | Yes | https://hire.help.lever.co/ | API | Daily | High | Expansion in physical security AI; backed by Ansa Capital and Battery Ventures. |
| Ethereal Machines | Indian Deeptech | https://etherealmachines.com | India | https://etherealmachines.com/careers | https://etherealmachines.com/blog | N/A | https://github.com/etherealmachines | N/A | Freshteam | Yes | https://developers.freshteam.com | API | Daily | Medium | Secured $28.5M from Avataar Ventures; merging software with precision manufacturing hardware. |
| Emergent | Indian DevTools | https://www.emergent.ai | India | https://www.emergent.ai/careers | https://www.emergent.ai/blog | N/A | https://github.com/emergent-ai | N/A | Greenhouse | Yes | https://developers.greenhouse.io/job-board.html | API | Daily | High | Raised $23M from Lightspeed; pioneering "vibe coding" paradigms and surpassing $100M ARR. |
| Oolka | Indian Fintech | https://www.oolka.com | India | https://www.oolka.com/careers | https://www.oolka.com/blog | N/A | N/A | N/A | Workable | Yes | https://resources.workable.com/api | API | Weekly | Medium | AI-powered fintech integrating agentic architectures into financial forecasting. |
| Dashverse | Indian EntTech | https://dashverse.io | India | https://dashverse.io/careers | https://dashverse.io/blog | N/A | N/A | N/A | Keka Hire | Yes | https://developers.keka.com/docs | API | Weekly | Low | Peak XV Partners backed; monitors Keka Hire endpoints for regional technical talent. |
| Portkey | Indian DevTools | https://portkey.ai | India | https://portkey.ai/careers | https://portkey.ai/blog | https://portkey.ai/blog/tag/engineering | https://github.com/portkey-ai | https://portkey.ai/rss.xml | Ashby | Yes | https://developers.ashbyhq.com | API | Daily | High | Potential $140M acquisition target by Palo Alto Networks; highly active OSS footprint. |
| Ringg AI | Indian AI | https://ringg.ai | India | https://ringg.ai/careers | N/A | N/A | N/A | N/A | Darwinbox | No | N/A | Playwright | Weekly | Medium | Voice AI startup; dynamic Darwinbox rendering demands headless browser scraping methodologies. |
| EtherealX | Indian Spacetech | https://etherealx.space | India | https://etherealx.space/careers | N/A | N/A | N/A | N/A | BambooHR | Yes | https://documentation.bamboohr.com/ | API | Weekly | Medium | In strategic talks for $20-25M funding; highlights the lack of spacetech unicorns in India. |
| Noon | Indian DevTools | https://noon.ai | India | https://noon.ai/careers | https://noon.ai/blog | N/A | N/A | N/A | Lever | Yes | https://hire.help.lever.co/ | API | Daily | High | Product design startup emerging from stealth with $44M; indicating aggressive UX/UI hiring. |
| Biopeak | Indian Healthtech | https://biopeak.health | India | https://biopeak.health/careers | N/A | N/A | N/A | N/A | Recruitee | Yes | https://api.recruitee.com/docs/ | API | Weekly | Low | Niche longevity startup backed by Nikhil Kamath; specialized bioinformatics recruitment. |
| Graph AI | Indian Healthtech | https://graph.ai | India | https://graph.ai/careers | N/A | N/A | N/A | N/A | Pinpoint | Yes | https://api.pinpointhq.com/docs | API | Weekly | Low | Drug safety platform utilizing structural graph data models for pharmacological prediction. |
| Zelo Electric | Indian EV | https://zeloelectric.com | India | https://zeloelectric.com/careers | N/A | N/A | N/A | N/A | Zoho Recruit | Yes | https://www.zoho.com/recruit/developer-guide/ | API | Weekly | Low | Developing affordable EVs; targets embedded systems and physical engineering cohorts. |
| ExtraaEdge | Indian SaaS | https://extraaedge.com | India | https://extraaedge.com/careers | https://extraaedge.com/blog | N/A | N/A | N/A | Freshteam | Yes | https://developers.freshteam.com/ | API | Weekly | Low | Edtech-focused CRM backed by predictive analytics and smart lead scoring models. |
| Influish | Indian Creator Econ | https://influish.com | India | https://influish.com/careers | N/A | N/A | N/A | N/A | Keka Hire | Yes | https://developers.keka.com/docs | HTML | Monthly | Low | Early-stage creator economy platform; DOM extraction viable if API limits constrain access. |

---

## ai_companies

| Name | Category | Website | Country | Career Page | Company Blog | Engineering Blog | GitHub Organization | RSS Feed | ATS Used | Public API Available | API Documentation | Crawl Method Recommendation | Suggested Crawl Frequency | Priority | Notes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Krutrim SI Designs | Indian AI Unicorn | https://www.olakrutrim.com | India | https://www.olakrutrim.com/careers | N/A | N/A | N/A | N/A | Greenhouse | Yes | https://developers.greenhouse.io/job-board.html | API | Daily | High | India's first AI unicorn; expanding silicon architecture and foundational model engineering. |
| Sarvam AI | Indian AI | https://www.sarvam.ai | India | https://www.sarvam.ai/careers | https://www.sarvam.ai/blog | N/A | https://github.com/sarvamai | N/A | Ashby | Yes | https://developers.ashbyhq.com/reference/job-board-api | API | Daily | High | Indic language model pioneer; backed heavily by Khosla Ventures and Peak XV Partners. |
| Neysa | Indian AI SaaS | https://neysa.ai | India | https://neysa.ai/careers | https://neysa.ai/blog | N/A | N/A | N/A | Lever | Yes | https://hire.help.lever.co/ | API | Daily | High | Enterprise AI infrastructure platform driving highly secure deployment models. |
| Wysa | Indian AI Health | https://www.wysa.io | India | https://www.wysa.io/careers | https://www.wysa.io/blog | N/A | N/A | N/A | Workable | Yes | https://resources.workable.com/api | API | Weekly | Medium | Emotionally intelligent conversational chatbots requiring deep NLP and psychology talent. |
| OpenAI | Global AI | https://openai.com | USA | https://openai.com/careers | https://openai.com/blog | https://openai.com/research | https://github.com/openai | https://openai.com/blog/rss.xml | Ashby | Yes | https://developers.ashbyhq.com/reference/job-board-api | API | Daily | High | Defines global AI taxonomies; Ashby implementation provides highly structured metadata. |
| Anthropic | Global AI | https://www.anthropic.com | USA | https://www.anthropic.com/careers | https://www.anthropic.com/news | https://www.anthropic.com/research | https://github.com/anthropics | N/A | Greenhouse | Yes | https://developers.greenhouse.io/job-board.html | API | Daily | High | Emphasizes constitutional AI and safety engineering; tracks organizational structure changes. |
| Hugging Face | Global AI | https://huggingface.co | USA/France | https://huggingface.co/careers | https://huggingface.co/blog | N/A | https://github.com/huggingface | N/A | Ashby | Yes | https://developers.ashbyhq.com/reference/job-board-api | API | Daily | High | Premier open-source AI platform; OSS repository tracking is critical for community alignment. |
| Scale AI | Global AI | https://scale.com | USA | https://scale.com/careers | https://scale.com/blog | N/A | https://github.com/scaleapi | N/A | Greenhouse | Yes | https://developers.greenhouse.io/job-board.html | API | Daily | High | Provides massive data labeling and RLHF infrastructure; scaling operations drastically. |
| Cohere | Global AI | https://cohere.com | Canada | https://cohere.com/careers | https://cohere.com/blog | N/A | https://github.com/cohere-ai | N/A | Lever | Yes | https://hire.help.lever.co/ | API | Daily | High | Enterprise NLP focus; tracks parallel expansion trajectories to US-based foundational models. |
| Midjourney | Global AI | https://www.midjourney.com | USA | https://www.midjourney.com/careers | N/A | N/A | N/A | N/A | Custom | No | N/A | Playwright | Weekly | Medium | Operates with a deliberately constrained, high-density research team; minimal ATS presence. |

---

## startup_news

Global signal sources — seeded with `company_id = NULL`.

| Name | Category | Website | Country | Career Page | Company Blog | Engineering Blog | GitHub Organization | RSS Feed | ATS Used | Public API Available | API Documentation | Crawl Method Recommendation | Suggested Crawl Frequency | Priority | Notes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| YourStory | Startup News | https://yourstory.com | India | N/A | N/A | N/A | N/A | https://yourstory.com/feed | N/A | No | N/A | RSS | Hourly | High | Primary aggregator for Indian funding rounds, product launches, and acquisition announcements. |
| Inc42 | Startup News | https://inc42.com | India | N/A | N/A | N/A | N/A | https://inc42.com/feed/ | N/A | No | N/A | RSS | Hourly | High | Essential for identifying early-stage Series A/B Indian startups exhibiting engineering expansion. |
| Entrackr | Startup News | https://entrackr.com | India | N/A | N/A | N/A | N/A | https://entrackr.com/feed/ | N/A | No | N/A | RSS | Hourly | High | Specializes in financial teardowns and unearthing stealth mode startups before public launch. |
| VCCircle | Startup News | https://www.vccircle.com | India | N/A | N/A | N/A | N/A | https://www.vccircle.com/feed | N/A | No | N/A | RSS | Daily | Medium | Tracks private equity and venture capital deal flow indicative of forthcoming hiring sprees. |
| DealStreetAsia | Startup News | https://www.dealstreetasia.com | Singapore | N/A | N/A | N/A | N/A | https://www.dealstreetasia.com/feed | N/A | No | N/A | RSS | Daily | Medium | Pan-Asian coverage including Indian startup expansions into Southeast Asian markets. |
| Tech In Asia | Startup News | https://www.techinasia.com | Singapore | N/A | N/A | N/A | N/A | https://www.techinasia.com/feed | N/A | No | N/A | RSS | Daily | Medium | Bridges the gap between Indian technical expansion and broader Asian capital structures. |
| EU-Startups | Startup News | https://www.eu-startups.com | Europe | N/A | N/A | N/A | N/A | https://www.eu-startups.com/feed/ | N/A | No | N/A | RSS | Daily | Medium | Tracks European expansion signals for Indian SaaS entities pivoting to global markets. |

---

## tech_news

Global signal sources — seeded with `company_id = NULL`.

| Name | Category | Website | Country | Career Page | Company Blog | Engineering Blog | GitHub Organization | RSS Feed | ATS Used | Public API Available | API Documentation | Crawl Method Recommendation | Suggested Crawl Frequency | Priority | Notes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| TechCrunch | Tech News | https://techcrunch.com | USA | N/A | N/A | N/A | N/A | https://techcrunch.com/feed/ | N/A | Yes | https://techcrunch.com/wp-json/wp/v2/posts | API | Hourly | High | WP-JSON API is significantly faster and richer in metadata than RSS for scraping global launches. |
| ETtech | Tech News | https://economictimes.indiatimes.com/tech | India | N/A | N/A | N/A | N/A | https://economictimes.indiatimes.com/tech/rssfeeds/13357200.cms | N/A | No | N/A | RSS | Hourly | High | Primary mainstream reporter of deeptech, SaaS, and AI hiring trends impacting the Indian market. |
| The Ken | Tech News | https://the-ken.com | India | N/A | N/A | N/A | N/A | https://the-ken.com/feed/ | N/A | No | N/A | HTML | Daily | Medium | Subscription-walled analytical journalism requiring HTML parsing for meta-description extraction. |
| Hacker News | Tech News | https://news.ycombinator.com | USA | N/A | N/A | N/A | https://github.com/HackerNews | https://news.ycombinator.com/rss | N/A | Yes | https://github.com/HackerNews/API | API | Hourly | High | Official Firebase API optimally monitors 'Show HN' and 'Who is Hiring?' threads for technical shifts. |
| VentureBeat | Tech News | https://venturebeat.com | USA | N/A | N/A | N/A | N/A | https://venturebeat.com/feed/ | N/A | No | N/A | RSS | Daily | Medium | Excellent source for monitoring transformative tech coverage and B2B SaaS integration signals. |
| HackerNoon | Tech News | https://hackernoon.com | USA | N/A | N/A | N/A | N/A | https://hackernoon.com/feed | N/A | No | N/A | RSS | Daily | Medium | Heavily focused on software engineering paradigms, Web3 deployments, and developer tooling. |
| Wired | Tech News | https://www.wired.com | USA | N/A | N/A | N/A | N/A | https://www.wired.com/feed/rss | N/A | No | N/A | RSS | Daily | Medium | Provides in-depth technology trend reporting and systemic market condition analyses. |
| The Verge | Tech News | https://www.theverge.com | USA | N/A | N/A | N/A | N/A | https://www.theverge.com/rss/index.xml | N/A | No | N/A | RSS | Daily | Medium | Tracks consumer electronics hardware launches affecting supply chain technical recruitment. |

---

## global_engineering_blogs

| Name | Category | Website | Country | Career Page | Company Blog | Engineering Blog | GitHub Organization | RSS Feed | ATS Used | Public API Available | API Documentation | Crawl Method Recommendation | Suggested Crawl Frequency | Priority | Notes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Airbnb Engineering | Global Engineering | https://medium.com/airbnb-engineering | USA | https://careers.airbnb.com | N/A | https://medium.com/airbnb-engineering | https://github.com/airbnb | https://medium.com/feed/airbnb-engineering | Greenhouse | No | N/A | RSS | Weekly | High | Excellent resource for data engineering strategies and distributed backend architecture insights. |
| Netflix TechBlog | Global Engineering | https://netflixtechblog.com | USA | https://jobs.netflix.com | N/A | https://netflixtechblog.com | https://github.com/netflix | https://netflixtechblog.com/feed | Workday | Yes | N/A | RSS | Weekly | High | Heavy emphasis on microservices, chaos engineering, and cloud infrastructure scale. |
| Uber Engineering | Global Engineering | https://eng.uber.com | USA | https://www.uber.com/us/en/careers/ | N/A | https://eng.uber.com | https://github.com/uber | https://eng.uber.com/feed/ | iCIMS | No | N/A | RSS | Weekly | High | Details systems engineering at extreme geospatial and temporal scale parameters. |
| Stripe Engineering | Global Engineering | https://stripe.com/blog/engineering | USA | https://stripe.com/jobs | https://stripe.com/blog | https://stripe.com/blog/engineering | https://github.com/stripe | https://stripe.com/blog/engineering/feed | Greenhouse | Yes | https://developers.greenhouse.io/ | RSS | Weekly | High | Impeccable API design philosophy; signals robust fintech and infrastructure scaling patterns. |
| Slack Engineering | Global Engineering | https://slack.engineering | USA | https://slack.com/careers | N/A | https://slack.engineering | https://github.com/slackhq | https://slack.engineering/feed/ | Workday | Yes | N/A | RSS | Weekly | High | Explores complex distributed systems, state management, and real-time enterprise messaging. |
| GitHub Engineering | Global Engineering | https://github.com/about | USA | https://github.com/about/careers | https://github.blog | https://github.blog/category/engineering/ | https://github.com/github | https://github.blog/category/engineering/feed/ | Greenhouse | Yes | https://developers.greenhouse.io/ | RSS | Weekly | High | The industry standard-bearer for CI/CD, version control, and deployment architecture discussions. |
| Spotify Engineering | Global Engineering | https://engineering.atspotify.com | Sweden | https://www.lifeatspotify.com | N/A | https://engineering.atspotify.com | https://github.com/spotify | https://engineering.atspotify.com/feed/ | Lever | Yes | https://hire.help.lever.co/ | RSS | Weekly | High | Focuses heavily on agile squad structures, audio processing, and recommendation algorithms. |
| Canva Engineering | Global Engineering | https://canvatechblog.com | Australia | https://www.canva.com/careers/ | N/A | https://canvatechblog.com | https://github.com/canva | https://canvatechblog.com/feed | Lever | Yes | https://hire.help.lever.co/ | RSS | Weekly | High | Technical deep-dives into front-end optimizations, rendering engines, and WebGL architectures. |
| Dropbox Tech | Global Engineering | https://dropbox.tech | USA | https://jobs.dropbox.com | N/A | https://dropbox.tech | https://github.com/dropbox | https://dropbox.tech/feed | Greenhouse | Yes | https://developers.greenhouse.io/ | RSS | Weekly | Medium | Analyzes massive-scale storage arrays, block synchronization, and client-side optimization. |
| Cloudflare Blog | Global Engineering | https://blog.cloudflare.com | USA | https://www.cloudflare.com/careers/ | N/A | https://blog.cloudflare.com | https://github.com/cloudflare | https://blog.cloudflare.com/rss/ | Greenhouse | Yes | https://developers.greenhouse.io/ | RSS | Weekly | High | Crucial for tracking advancements in network edge infrastructure, routing, and cybersecurity standards. |

---

## vcs

Global signal sources — seeded with `company_id = NULL`.

| Name | Category | Website | Country | Career Page | Company Blog | Engineering Blog | GitHub Organization | RSS Feed | ATS Used | Public API Available | API Documentation | Crawl Method Recommendation | Suggested Crawl Frequency | Priority | Notes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Peak XV Partners | Venture Capital | https://www.peakxv.com | India | N/A | https://www.peakxv.com/perspectives/ | N/A | N/A | N/A | N/A | No | N/A | HTML | Daily | High | The definitive signal for Indian seed/Series A; backs unicorns like Sarvam AI. |
| Accel India | Venture Capital | https://www.accel.com/india | India | N/A | https://www.accel.com/insights | N/A | N/A | N/A | N/A | No | N/A | HTML | Weekly | High | Heavy focus on developer tools and Indian AI; co-invests deeply with global tech giants. |
| Lightspeed India | Venture Capital | https://lsvp.com/india/ | India | N/A | https://lsvp.com/stories/ | N/A | N/A | https://lsvp.com/feed/ | N/A | No | N/A | RSS | Weekly | High | Major backer of SaaS and AI platforms; feed extraction yields high-signal investment data. |
| Nexus Venture Partners | Venture Capital | https://nexusvp.com | India/USA | N/A | https://nexusvp.com/insights/ | N/A | N/A | N/A | N/A | No | N/A | HTML | Weekly | High | Cross-border fund investing actively in global SaaS operations originating from India. |
| Elevation Capital | Venture Capital | https://elevationcapital.com | India | N/A | https://elevationcapital.com/perspectives | N/A | N/A | N/A | N/A | No | N/A | HTML | Weekly | High | Recently closed a $500M fund specifically targeting the AI application and integration layer. |
| Kalaari Capital | Venture Capital | https://www.kalaari.com | India | N/A | https://www.kalaari.com/perspectives/ | N/A | N/A | N/A | N/A | No | N/A | HTML | Weekly | Medium | Concentrates heavily on enterprise operations, cyber defense, and AI infrastructure investments. |
| Andreessen Horowitz | Venture Capital | https://a16z.com | USA | N/A | https://a16z.com/articles/ | N/A | https://github.com/a16z | https://a16z.com/feed/ | N/A | No | N/A | RSS | Daily | High | Globally defines structural tech trends which cascade into Indian outsourcing and product requirements. |
| Tomasz Tunguz | Venture Capital | https://tomtunguz.com | USA | N/A | https://tomtunguz.com | N/A | N/A | https://tomtunguz.com/index.xml | N/A | No | N/A | RSS | Daily | High | Provides the industry's best mathematical breakdowns of SaaS metrics, aiding in compensation forecasting. |
| Hunter Walk | Venture Capital | https://hunterwalk.com | USA | N/A | https://hunterwalk.com | N/A | N/A | https://hunterwalk.com/feed/ | N/A | No | N/A | RSS | Weekly | Medium | Crucial insights on product management lifecycles and early-stage startup ecosystem mechanics. |
| WestBridge Capital | Venture Capital | https://www.westbridgecap.com | India | N/A | N/A | N/A | N/A | N/A | N/A | No | N/A | HTML | Monthly | High | Significant late-stage backer; injection of funds (e.g., UnifyApps) immediately precipitates mass hiring. |

---

## accelerators

Global signal sources — seeded with `company_id = NULL`.

| Name | Category | Website | Country | Career Page | Company Blog | Engineering Blog | GitHub Organization | RSS Feed | ATS Used | Public API Available | API Documentation | Crawl Method Recommendation | Suggested Crawl Frequency | Priority | Notes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Y Combinator | Accelerator | https://www.ycombinator.com | USA | N/A | https://blog.ycombinator.com | N/A | N/A | https://blog.ycombinator.com/feed/ | N/A | Yes | https://github.com/HackerNews/API | RSS | Daily | High | Expanding aggressively into India; shifting focus toward hyperlocal consumer and logistics products. |
| Surge | Accelerator | https://www.surgeahead.com | India | N/A | N/A | N/A | N/A | N/A | N/A | No | N/A | HTML | Weekly | High | The premier early-stage startup scale-up program explicitly targeting India and Southeast Asia. |
| 500 Global | Accelerator | https://500.co | USA | N/A | https://500.co/blog | N/A | N/A | N/A | N/A | No | N/A | HTML | Weekly | High | Executes transformative founder programs globally; high density of diverse early-stage cohorts. |
| Techstars | Accelerator | https://www.techstars.com | USA | N/A | https://www.techstars.com/newsroom | N/A | N/A | N/A | N/A | No | N/A | HTML | Weekly | Medium | Maintains a massive global footprint featuring highly specialized regional and thematic chapters. |
| Blume Ventures | Accelerator | https://blume.vc | India | N/A | https://blume.vc/perspectives | N/A | N/A | N/A | N/A | No | N/A | HTML | Weekly | Medium | Operates Lead Tribe and various early-stage frameworks with deep ties into B2B software scaling. |

---

## rss_feeds

Mostly duplicates of rows above; the seed script deduplicates by URL.

| Name | Category | Website | Country | Career Page | Company Blog | Engineering Blog | GitHub Organization | RSS Feed | ATS Used | Public API Available | API Documentation | Crawl Method Recommendation | Suggested Crawl Frequency | Priority | Notes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| TechCrunch Startups | RSS Feed | https://techcrunch.com | USA | N/A | N/A | N/A | N/A | https://techcrunch.com/category/startups/feed/ | N/A | Yes | https://techcrunch.com/wp-json/ | RSS | Hourly | High | Delivers an exceptionally high volume of global funding events and product launch data. |
| Y Combinator Blog | RSS Feed | https://blog.ycombinator.com | USA | N/A | N/A | N/A | N/A | https://blog.ycombinator.com/feed/ | N/A | No | N/A | RSS | Daily | High | Essential ingestion point for early-stage founder philosophy and emergent tech market signals. |
| HackerNews Global | RSS Feed | https://news.ycombinator.com | USA | N/A | N/A | N/A | N/A | https://news.ycombinator.com/rss | N/A | Yes | https://github.com/HackerNews/API | API | Hourly | High | Captures raw technical sentiment, organic software launches, and unvarnished engineering pain points. |
| OpenAI Blog | RSS Feed | https://openai.com/blog | USA | N/A | N/A | N/A | N/A | https://openai.com/blog/rss.xml | N/A | No | N/A | RSS | Daily | High | Monitors updates to foundational models which subsequently dictate startup application layer architectures. |
| ETtech Feed | RSS Feed | https://economictimes.indiatimes.com | India | N/A | N/A | N/A | N/A | https://economictimes.indiatimes.com/tech/rssfeeds/13357200.cms | N/A | No | N/A | RSS | Hourly | High | Unparalleled localized feed for Indian startup acquisitions, leadership shuffles, and regulatory shifts. |

---

## github_orgs

All duplicates of companies above; the seed script deduplicates by URL.

| Name | Category | Website | Country | Career Page | Company Blog | Engineering Blog | GitHub Organization | RSS Feed | ATS Used | Public API Available | API Documentation | Crawl Method Recommendation | Suggested Crawl Frequency | Priority | Notes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Razorpay GitHub | GitHub Org | https://github.com/razorpay | India | N/A | N/A | N/A | https://github.com/razorpay | N/A | N/A | Yes | https://docs.github.com/en/rest | API | Weekly | High | Extremely active OSS repository; spikes in new repo creation correlate directly with hiring drives. |
| Freshworks GitHub | GitHub Org | https://github.com/freshworks | India | N/A | N/A | N/A | https://github.com/freshworks | N/A | N/A | Yes | https://docs.github.com/en/rest | API | Weekly | High | Crucial for analyzing internal tooling publication patterns and anticipating technical stack expansions. |
| Swiggy GitHub | GitHub Org | https://github.com/swiggy-private | India | N/A | N/A | N/A | https://github.com/swiggy-private | N/A | N/A | Yes | https://docs.github.com/en/rest | API | Weekly | High | Primary open-source contributions focus heavily on algorithmic optimization and delivery logic architecture. |
| Zepto GitHub | GitHub Org | https://github.com/zepto | India | N/A | N/A | N/A | https://github.com/zepto | N/A | N/A | Yes | https://docs.github.com/en/rest | API | Weekly | High | High frequency of foundational engineering expansion indicative of aggressive rapid-commerce scaling. |
| Sarvam AI GitHub | GitHub Org | https://github.com/sarvamai | India | N/A | N/A | N/A | https://github.com/sarvamai | N/A | N/A | Yes | https://docs.github.com/en/rest | API | Weekly | High | Tracks the publication of Indic foundational LLM weights, localized tokenizer tools, and specialized AI kits. |
| OpenAI GitHub | GitHub Org | https://github.com/openai | USA | N/A | N/A | N/A | https://github.com/openai | N/A | N/A | Yes | https://docs.github.com/en/rest | API | Daily | High | Represents an essential global signal for tracking state-of-the-art machine learning deployment stacks. |
