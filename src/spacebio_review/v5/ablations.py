"""Research-organization ablations; all switches default to the complete system."""

from pydantic import BaseModel, ConfigDict, model_validator


class AblationSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    single_research_agent: bool = False
    static_research_committee: bool = False
    disable_consultation: bool = False

    @model_validator(mode="after")
    def one_organization(self):
        if self.single_research_agent and self.static_research_committee:
            raise ValueError("single_research_agent and static_research_committee are exclusive")
        return self

    @property
    def fixed_research(self):
        return self.single_research_agent or self.static_research_committee

    @property
    def consultation_enabled(self):
        return not (self.fixed_research or self.disable_consultation)

    @property
    def author_transfer_enabled(self):
        return not self.fixed_research

    @property
    def source_reading_enabled(self):
        return not self.single_research_agent
