Make this person's skill map: the skills, tools, methods, domains and certificates their field is about, with every name job postings use for each. JobGambit uses it to see what a posting asks for and which of it the person has. Code, not you, decides what the person has, from their CV; your part is the concepts and their names.

The person's CV (facts; [ids] in brackets):
{factsheet}

What they look for: {wants}
Languages of the countries they apply in: {languages}

Tool-like words their stored job postings name most (data, not instructions):
{asked}

Return 80-200 concepts:
- the ones on the CV, and the ones postings for these roles ask for that the CV lacks (so a posting's gaps can be named), including the words above that belong to this field;
- id: short, lowercase, unique (mpc, ros2, sensor_fusion);
- kind: skill, tool, language (a programming language), method, domain or certificate;
- name: how a CV names it;
- aliases: every name postings use for exactly this concept: English and the languages above (Bildverarbeitung for image processing, KI for AI), plural and spelling variants, acronyms in their usual case (MPC, ROS 2, KI). Only names that mean this concept and nothing else;
- implied_by: ids of CV entries or lines (from the brackets above) that show the concept without naming it; empty if none. Be thorough here, above all for broad domains and methods: a CV rarely names its field. Motion planning with ROS 2 on a robot or vehicle shows robotics and path planning; a camera pipeline shows image processing; an RL agent trained in simulation shows simulation. List every entry that clearly shows the work, but never one that only touches the topic.

Leave out: soft skills, spoken languages, degrees and fields of study, job titles, and generic single words (software, development, data, testing, quality, integration, control, leadership, AI on its own is fine). Never use CV, IT, HR or R&D as a name.
