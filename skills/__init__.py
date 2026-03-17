"""Skills package for quarterly earnings research."""
from skills.select_company import SelectCompanySkill
from skills.research_company import ResearchCompanySkill
from skills.get_reports import GetReportsSkill
from skills.get_numbers import GetNumbersSkill
from skills.extract_goals import ExtractGoalsSkill
from skills.analyze_tone import AnalyzeToneSkill
from skills.analyze_price import AnalyzePriceSkill
from skills.get_logo import GetLogoSkill
from skills.compare_reports import CompareReportsSkill
from skills.generate_report import GenerateReportSkill
from skills.ten_point_analysis import TenPointAnalysisSkill
from skills.animate import AnimateSkill

__all__ = [
    "SelectCompanySkill",
    "ResearchCompanySkill",
    "GetReportsSkill",
    "GetNumbersSkill",
    "ExtractGoalsSkill",
    "AnalyzeToneSkill",
    "AnalyzePriceSkill",
    "GetLogoSkill",
    "CompareReportsSkill",
    "GenerateReportSkill",
    "TenPointAnalysisSkill",
    "AnimateSkill",
]